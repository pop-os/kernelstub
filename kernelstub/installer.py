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
"""

import os, shutil, logging, platform, gzip, subprocess

from pathlib import Path

class FileOpsError(Exception):
    pass

class Installer():

    loader_dir = '/boot/efi/loader'
    entry_dir = '/boot/efi/loader/entries'
    os_dir_name = 'linux-kernelstub'
    work_dir = '/boot/efi/EFI/'
    old_kernel = True

    def __init__(self, nvram, opsys, drive):
        self.log = logging.getLogger('kernelstub.Installer')
        self.log.debug('loaded kernelstub.Installer')

        self.nvram = nvram
        self.opsys = opsys
        self.drive = drive

        self.work_dir = os.path.join(self.drive.esp_path, "EFI")
        self.loader_dir = os.path.join(self.drive.esp_path, "loader")
        self.entry_dir = os.path.join(self.loader_dir, "entries")
        self.os_dir_name = "%s-%s" % (self.opsys.name, self.drive.root_uuid)
        self.os_folder = os.path.join(self.work_dir, self.os_dir_name)
        self.kernel_dest = os.path.join(self.os_folder, self.opsys.kernel_name)
        self.initrd_dest = os.path.join(self.os_folder, self.opsys.initrd_name)

        if not os.path.exists(self.loader_dir):
            os.makedirs(self.loader_dir)
        if not os.path.exists(self.entry_dir):
            os.makedirs(self.entry_dir)

    def setup_unified_kernel(self, kernel_opts, old=False, overwrite=False, simulate=False):
        if old:
            suffix = 'previous'
            kernel_path = self.opsys.old_kernel_path
            initrd_path = self.opsys.old_initrd_path
        else:
            suffix = 'current'
            kernel_path = self.opsys.kernel_path
            initrd_path = self.opsys.initrd_path

        tmp_dir = os.path.join('/tmp/kernelstub', suffix)
        self.ensure_dir(tmp_dir, simulate=simulate)

        cmdline_path = os.path.join(tmp_dir, 'cmdline')
        if simulate:
            self.log.info('Simulate creating file: %s with contents %s' % (cmdline_path, kernel_opts))
        else:
            with open(cmdline_path, mode='w') as f:
                f.write(kernel_opts)

        self.log.info('Creating unified Linux EFI executable (%s)' % (suffix))
        kernel_unsigned_efi_path = os.path.join(tmp_dir, 'linux-unsigned.efi')
        objcopy = [
            'objcopy',
            '--add-section', '.osrel=/usr/lib/os-release', '--change-section-vma', '.osrel=0x20000',
            '--add-section', '.cmdline=' + cmdline_path, '--change-section-vma', '.cmdline=0x30000',
            '--add-section', '.linux=' + kernel_path, '--change-section-vma', '.linux=0x2000000',
            '--add-section', '.initrd=' + initrd_path, '--change-section-vma', '.initrd=0x3000000',
            '/usr/lib/systemd/boot/efi/linuxx64.efi.stub', kernel_unsigned_efi_path
        ]
        if simulate:
            self.log.info('Simulate running command: %s' % (objcopy))
        else:
            subprocess.check_call(objcopy)

        sign_crt = '/etc/kernelstub/mok.crt'
        sign_key = '/etc/kernelstub/mok.key'
        if os.path.exists(sign_crt) and os.path.exists(sign_key):
            self.log.info('Signing unified Linux EFI executable (%s)' % (suffix))
            kernel_signed_efi_path = os.path.join(tmp_dir, 'signed.efi')
            sbsign = [
                'sbsign',
                '--cert', sign_crt,
                '--key', sign_key,
                '--output', kernel_signed_efi_path,
                kernel_unsigned_efi_path
            ]
            if simulate:
                self.log.info('Simulate running command: %s' % (sbsign))
            else:
                subprocess.check_call(sbsign)
            kernel_efi_path = kernel_signed_efi_path
        else:
            kernel_efi_path = kernel_unsigned_efi_path

        self.log.info('Copying unified Linux EFI executable to ESP (%s)' % (suffix))
        kernel_efi_dest_dir = os.path.join(self.work_dir, 'Linux')
        self.ensure_dir(kernel_efi_dest_dir, simulate=simulate)
        kernel_efi_name = self.opsys.name + '-' + suffix + '-' + self.drive.root_uuid + '.efi'
        kernel_efi_dest = os.path.join(kernel_efi_dest_dir, kernel_efi_name)
        self.copy_files(
            kernel_efi_path,
            kernel_efi_dest,
            simulate=simulate
        )

        if not overwrite and not simulate:
            if not os.path.exists('%s/loader.conf' % self.loader_dir):
                overwrite = True

        if overwrite and not old and not simulate:
            self.ensure_dir(self.loader_dir)
            with open(
                '%s/loader.conf' % self.loader_dir, mode='w') as loader:

                default_line = 'default %s\n' % kernel_efi_name
                loader.write(default_line)

    def backup_old(self, kernel_opts, setup_loader=False, simulate=False):
        self.log.info('Backing up old kernel')

        old_path = Path(self.opsys.old_kernel_path).resolve()
        new_path = Path(self.opsys.kernel_path).resolve()
        if old_path == new_path:
            self.log.info('No old kernel found, skipping')
            return 0

        old_kernel_name = "%s-previous.efi" % self.opsys.kernel_name
        old_kernel_dest = os.path.join(self.os_folder, old_kernel_name)
        try:
            self.copy_files(
                self.opsys.old_kernel_path,
                old_kernel_dest,
                simulate=simulate)
        except:
            self.log.debug('Couldn\'t back up old kernel. There\'s ' +
                           'probably only one kernel installed.')
            self.old_kernel = False
            pass

        old_initrd_name = "%s-previous" % self.opsys.initrd_name
        old_initrd_dest = os.path.join(self.os_folder, old_initrd_name)
        try:
            self.copy_files(
                self.opsys.old_initrd_path,
                old_initrd_dest,
                simulate=simulate)
        except:
            self.log.debug('Couldn\'t back up old initrd.img. There\'s ' +
                           'probably only one kernel installed.')
            self.old_kernel = False
            pass

        if setup_loader and self.old_kernel:
            self.ensure_dir(self.entry_dir)
            linux_line = '/EFI/%s-%s/%s-previous.efi' % (self.opsys.name,
                                                         self.drive.root_uuid,
                                                         self.opsys.kernel_name)
            initrd_line = '/EFI/%s-%s/%s-previous' % (self.opsys.name,
                                                      self.drive.root_uuid,
                                                      self.opsys.initrd_name)
            self.make_loader_entry(
                self.opsys.name_pretty,
                linux_line,
                initrd_line,
                kernel_opts,
                os.path.join(self.entry_dir, '%s-oldkern' % self.opsys.name))

    def setup_kernel(self, kernel_opts, setup_loader=False, overwrite=False, simulate=False):
        self.log.info('Copying Kernel into ESP')
        self.kernel_dest = os.path.join(
            self.os_folder,
            "%s.efi" % self.opsys.kernel_name)
        self.ensure_dir(self.os_folder, simulate=simulate)
        self.log.debug('kernel being copied to %s' % self.kernel_dest)

        try:
            arch = platform.machine()
            if arch == "arm64" or arch == "aarch64":
                self.gunzip_files(
                    self.opsys.kernel_path,
                    self.kernel_dest,
                    simulate=simulate)
            else:
                self.copy_files(
                    self.opsys.kernel_path,
                    self.kernel_dest,
                    simulate=simulate)

        except FileOpsError as e:
            self.log.exception(
                'Couldn\'t copy the kernel onto the ESP!\n' +
                'This is a critical error and we cannot continue. Check your ' +
                'settings to see if there is a typo. Otherwise, check ' +
                'permissions and try again.')
            self.log.debug(e)
            exit(170)

        self.log.info('Copying initrd.img into ESP')
        self.initrd_dest = os.path.join(self.os_folder, self.opsys.initrd_name)
        try:
            self.copy_files(
                self.opsys.initrd_path,
                self.initrd_dest,
                simulate=simulate)

        except FileOpsError as e:
            self.log.exception('Couldn\'t copy the initrd onto the ESP!\n' +
                               'This is a critical error and we cannot ' +
                               'continue. Check your settings to see if ' +
                               'there is a typo. Otherwise, check permissions ' +
                               'and try again.')
            self.log.debug(e)
            exit(171)

        self.log.debug('Copy complete')

        if setup_loader:
            self.log.info('Setting up loader.conf configuration')
            linux_line = '/EFI/%s-%s/%s.efi' % (self.opsys.name,
                                                self.drive.root_uuid,
                                                self.opsys.kernel_name)
            initrd_line = '/EFI/%s-%s/%s' % (self.opsys.name,
                                             self.drive.root_uuid,
                                             self.opsys.initrd_name)
            if simulate:
                self.log.info("Simulate creation of entry...")
                self.log.info('Loader entry: %s/%s-current\n' %(self.entry_dir,
                                                                self.opsys.name) +
                              'title %s\n' % self.opsys.name_pretty +
                              'linux %s\n' % linux_line +
                              'initrd %s\n' % initrd_line +
                              'options %s\n' % kernel_opts)
                return 0

            if not overwrite:
                if not os.path.exists('%s/loader.conf' % self.loader_dir):
                    overwrite = True

            if overwrite:
                self.ensure_dir(self.loader_dir)
                with open(
                    '%s/loader.conf' % self.loader_dir, mode='w') as loader:

                    default_line = 'default %s-current\n' % self.opsys.name
                    loader.write(default_line)

            self.ensure_dir(self.entry_dir)
            self.make_loader_entry(
                self.opsys.name_pretty,
                linux_line,
                initrd_line,
                kernel_opts,
                os.path.join(self.entry_dir, '%s-current' % self.opsys.name))





    def setup_stub(self, kernel_opts, simulate=False):
        self.log.info("Setting up Kernel EFISTUB loader...")
        self.copy_cmdline(simulate=simulate)
        self.nvram.update()

        if self.nvram.os_entry_index >= 0:
            self.log.info("Deleting old boot entry")
            self.nvram.delete_boot_entry(self.nvram.order_num, simulate)

        else:
            self.log.debug("No old entry found, skipping removal.")

        self.nvram.add_entry(self.opsys, self.drive, kernel_opts, simulate)
        self.nvram.update()
        nvram_lines = "\n".join(self.nvram.nvram)
        self.log.info('NVRAM configured, new values: \n\n%s\n' % nvram_lines)

    def copy_cmdline(self, simulate):
        self.copy_files(
            '/proc/cmdline',
            self.os_folder,
            simulate = simulate
        )


    def make_loader_entry(self, title, linux, initrd, options, filename):
        self.log.info('Making entry file for %s' % title)
        with open('%s.conf' % filename, mode='w') as entry:
            entry.write('title %s\n' % title)
            entry.write('linux %s\n' % linux)
            entry.write('initrd %s\n' % initrd)
            entry.write('options %s\n' % options)
        self.log.debug('Entry created!')

    def ensure_dir(self, directory, simulate=False):
        if not simulate:
            try:
                os.makedirs(directory, exist_ok=True)
                return True
            except Exception as e:
                self.log.exception('Couldn\'t make sure %s exists.' % directory)
                self.log.debug(e)
                return False

    def gunzip_files(self, src, dest, simulate): # Decompress file src to dest
        if simulate:
            self.log.info('Simulate decompressing: %s => %s' % (src, dest))
            return True
        else:
            try:
                self.log.debug('Decompressing: %s => %s' % (src, dest))
                with gzip.open(src, 'rb') as in_obj:
                    with open(dest, 'wb') as out_obj:
                        shutil.copyfileobj(in_obj, out_obj)
                return True
            except Exception as e:
                self.log.debug(e)
                raise FileOpsError("Could not decompress one or more files.")
                return False


    def copy_files(self, src, dest, simulate): # Copy file src into dest
        if simulate:
            self.log.info('Simulate copying: %s => %s' % (src, dest))
            return True
        else:
            try:
                self.log.debug('Copying: %s => %s' % (src, dest))
                shutil.copy(src, dest)
                return True
            except Exception as e:
                self.log.debug(e)
                raise FileOpsError("Could not copy one or more files.")
                return False
