#!/usr/bin/python3

"""
 kernelstub
 The automatic manager for using the Linux Kernel EFI Stub to boot

 Copyright 2017-2018 Ian Santopietro <isantop@gmail.com>

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.

Please see the provided LICENSE.txt file for additional distribution/copyright
terms.

 This program will automatically keep a copy of the Linux Kernel image and your
 initrd.img located on your EFI System Partition (ESP) on an EFI-compatible
 system. The benefits of this approach include being able to boot Linux directly
 and bypass the GRUB bootloader in many cases, which can save 4-6 seconds during
 boot on a system with a fast drive. Please note that there are no guarantees
 about boot time saved using this method.

 For maximum safety, kernelstub recommends leaving an entry for GRUB in your
 system's ESP and NVRAM configuration, as GRUB allows you to modify boot
 parameters per-boot, which loading the kernel directly cannot do. The only
 other way to do this is to use an EFI shell to manually load the kernel and
 give it parameters from the EFI Shell. You can do this by using:

 fs0:> vmlinuz-image initrd=EFI/path/to/initrd/stored/on/esp options

 kernelstub will load parameters from the /etc/default/kernelstub config file.
"""

import logging, subprocess, shutil, os, platform

from . import drive as Drive
from . import nvram as Nvram
from . import opsys as Opsys
from . import installer as Installer
from . import config as Config

class CmdLineError(Exception):
    pass

class Kernelstub():

    def main(self, args): # Do the thing

        file_level = 'INFO'

        verbosity = 0
        if args.verbosity:
            verbosity = args.verbosity
        if verbosity > 2:
            verbosity = 2

        if args.print_config:
            verbosity = 1

        level = {
            0 : 'WARNING',
            1 : 'INFO',
            2 : 'DEBUG',
        }

        console_level = level[verbosity]

        log = logging.getLogger('kernelstub')
        console = logging.StreamHandler()
        stream_fmt = logging.Formatter('%(name)-21s: %(levelname)-8s %(message)s')
        console.setFormatter(stream_fmt)
        log.addHandler(console)
        log.setLevel(console_level)
        log.debug('Logging set up')

        # Figure out runtime options
        no_run = False
        if args.dry_run:
            no_run = True

        config = Config.Config()
        configuration = config.config['user']

        if args.esp_path:
            configuration['esp_path'] = args.esp_path

        root_path = "/"
        if args.root_path:
            root_path = args.root_path

        opsys = Opsys.OS()

        if args.kernel_path:
            log.debug(
                'Manually specified kernel path:\n ' +
                '               %s' % args.kernel_path)
            opsys.kernel_path = args.kernel_path
        else:
            opsys.kernel_path = os.path.join(root_path, opsys.kernel_name)

        if args.initrd_path:
            log.debug(
                'Manually specified initrd path:\n ' +
                '               %s' % args.initrd_path)
            opsys.initrd_path = args.initrd_path
        else:
            opsys.initrd_path = os.path.join(root_path, opsys.initrd_name)

        if not os.path.exists(opsys.kernel_path):
            log.critical('Can\'t find the kernel image! \n\n'
                         'Please use the --kernel-path option to specify '
                         'the path to the kernel image')
            exit(1)

        if not os.path.exists(opsys.initrd_path):
            log.critical('Can\'t find the initrd image! \n\n'
                         'Please use the --initrd-path option to specify '
                         'the path to the initrd image')
            exit(1)

        # Check for kernel parameters. Without them, stop and fail
        if args.k_options:
            configuration['kernel_options'] = args.k_options
        else:
            try:
                configuration['kernel_options']
            except KeyError:
                error = ("cmdline was 'InvalidConfig'\n\n"
                         "Could not find any valid configuration. This "
                         "probably means that the configuration file is "
                         "corrupt. Either remove it to regenerate it from"
                         "default or fix the existing one.")
                log.critical(error)
                raise CmdLineError("No Kernel Parameters found")
                exit(2)

        log.debug(config.print_config())

        if args.setup_loader:
            configuration['setup_loader'] = args.setup_loader

        if args.install_stub:
            configuration['manage_mode'] = False

        if args.manage_mode:
            configuration['manage_mode'] = True

        force = False
        if args.force_update:
            force = True
        if configuration['force_update'] == True:
            force = True

        # Check our configuration to make sure it's good
        try:
            kernel_opts = configuration['kernel_options']
            esp_path = configuration['esp_path']
            setup_loader = configuration['setup_loader']
            manage_mode = configuration['manage_mode']
            force_update = configuration['force_update']

        except KeyError:
            log.critical(
                'Malformed configuration! \n'
                'The configuration we got is bad, and we can\'nt continue. '
                'Please check the config files and make sure they are correct. '
                'If you can\'t figure it out, then deleting them should fix '
                'the errors and cause kernelstub to regenerate them from '
                'Default. \n\n You can use "-vv" to get the configuration used.')
            log.debug('Configuration we got: \n\n%s' % config.print_config())
            exit(4)

        drive = Drive.Drive(root_path=root_path, esp_path=esp_path)
        nvram = Nvram.NVRAM(opsys.name, opsys.version)
        installer = Installer.Installer(nvram, opsys, drive)

        # Log some helpful information, to file and optionally console
        info = (
            '    OS:..................%s %s\n'   %(opsys.name_pretty,opsys.version) +
            '    Root partition:....../dev/%s\n' % drive.root_fs +
            '    Root FS UUID:........%s\n'      % drive.root_uuid +
            '    ESP Path:............%s\n'      % esp_path +
            '    ESP Partition:......./dev/%s\n' % drive.esp_fs +
            '    ESP Partition #:.....%s\n'      % drive.esp_num +
            '    NVRAM entry #:.......%s\n'      % nvram.os_entry_index +
            '    Boot Variable #:.....%s\n'      % nvram.order_num +
            '    Kernel Boot Options:.%s\n'      % kernel_opts +
            '    Kernel Image Path:...%s\n'      % opsys.kernel_path +
            '    Initrd Image Path:...%s\n'      % opsys.initrd_path)

        log.info('System information: \n\n%s' % info)

        if args.print_config:
            all_config = (
                '   Kernel options:................%s\n' % configuration['kernel_options'] +
                '   ESP Location:..................%s\n' % configuration['esp_path'] +
                '   Management Mode:...............%s\n' % configuration['manage_mode'] +
                '   Install Loader configuration:..%s\n' % configuration['setup_loader'])
            log.info('Configuration details: \n\n%s' % all_config)
            exit(0)

        log.debug('Setting up boot...')

        kopts = 'root=UUID=%s ro %s' % (drive.root_uuid, kernel_opts)
        log.debug('kopts: %s' % kopts)

        installer.setup_kernel(
            kopts,
            setup_loader=setup_loader,
            overwrite=force,
            simulate=no_run)
        try:
            installer.backup_old(
                kopts,
                setup_loader=setup_loader,
                simulate=no_run)
        except Exception as e:
            log.error('Couldn\'t back up old kernel. \nThis might just mean ' +
                      'You don\'t have an old kernel installed. If you do, try' +
                      'With -vv to see debuging information')
            log.debug(e)

        if not manage_mode:
            installer.setup_stub(kopts, simulate=no_run)

        installer.copy_cmdline(simulate=no_run)

        config.config['user'] = configuration
        config.save_config()

        return 0

