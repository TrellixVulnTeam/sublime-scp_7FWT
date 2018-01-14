import os
import tarfile
import tempfile

import sublime
import sublime_plugin

from .core import commonpath
from .core import scpfolder
from .core import task
from .core.progress import Progress

from .core.scpclient import SCPCommandError
from .core.scpclient import SCPNotConnectedError

TEMPLATE = """
{
    // remote host name or IP address
    "host": "192.168.0.1",
    "port": 22,
    "user": "guest",
    "passwd": "guest",
    // remote path to use as root
    "path": "/home/guest"
}
""".lstrip()


class _ScpWindowCommand(sublime_plugin.WindowCommand):

    def is_visible(self, paths=None):
        """Menu item is visible, if connection is established."""
        return any(
            scpfolder.is_connected(path)
            for path in self.ensure_paths(paths)
        )

    def run(self, paths=None):
        task.call_func(self.executor, self.ensure_paths(paths))

    def ensure_paths(self, paths):
        """If no path was provided, use active view's file name."""
        if paths:
            return paths
        view = self.window.active_view()
        name = view.file_name() if view else None
        return [name] if name and os.path.exists(name) else []


class ScpMapToRemoteCommand(_ScpWindowCommand):

    def is_visible(self, paths=None):
        """Menu is visible if no mapping exists already."""
        return not any(
            scpfolder.root_dir(path)
            for path in self.ensure_paths(paths)
        )

    def run(self, paths=None):
        for path in self.ensure_paths(paths):
            self.window.run_command("open_file", {
                "file": os.path.join(path, ".scp"), "contents": TEMPLATE})
            self.window.active_view().assign_syntax("JSON.sublime-syntax")


class ScpConnectCommand(_ScpWindowCommand):

    def __init__(self, window):
        super().__init__(window)
        self.thread = None

    def is_enabled(self, paths=None):
        """Disable command while connection is being established."""
        return not self.thread

    def is_visible(self, paths=None):
        """Menu item is visible if mapping exists but offline."""
        return any(
            scpfolder.root_dir(path) and not scpfolder.is_connected(path)
            for path in self.ensure_paths(paths)
        )

    def run(self, paths=None):
        self.thread = task.call_func(self.executor, self.ensure_paths(paths))

    def executor(self, task, paths):
        with Progress("Connecting...") as progress:
            if all(scpfolder.connect(path) for path in paths):
                progress.done("SCP: Connected!")
            else:
                progress.done("SCP: Connection failed!")
        self.thread = None


class ScpDisconnectCommand(_ScpWindowCommand):

    def run(self, paths=None):
        for path in self.ensure_paths(paths):
            scpfolder.disconnect(path)
        sublime.status_message("SCP: Disconnected!")


class ScpCancelCommand(_ScpWindowCommand):

    def is_enabled(self, paths=None):
        """Enable command if an operation is in progress."""
        return task.busy()

    def run(self, paths=None):
        """Abort all queued and active opera"""
        task.cancel_all()
        for path in self.ensure_paths(paths):
            try:
                scpfolder.connection(path).cancel()
            except SCPNotConnectedError:
                pass
        sublime.status_message("SCP: Aborted!")


class ScpGetCommand(_ScpWindowCommand):

    def executor(self, task, paths):
        groups = {}
        for path in paths:
            if any(f in path for f in ('.scp', '.git')):
                continue
            try:
                conn = scpfolder.connection(path)
                groups.setdefault(conn, []).append(path)
            except SCPNotConnectedError:
                pass

        for conn, paths in groups.items():
            if len(paths) == 1 and os.path.isfile(paths[0]):
                # use simple upload for single files
                conn.getfile(paths[0])
                msg = "SCP: Downloaded %s!" % paths[0]
                sublime.status_message(msg)
            else:
                # use tarfile upload for multiple files and dirs
                self.gettree(conn, paths)

    def gettree(self, conn, paths):
        """
        Download several folders and files to the remote host.

        Uploading many files via scp is horribly slow. To work around that
        the following steps are performed:
        1. Pack all files given via `paths` into a single tar-file with
           relative paths based on the mapped folder.
        2. Upload the tar-file to the remote's /tmp/ folder.
        3. Untar the file on the remote host and delete it.
        """
        # find common root directory of all paths
        local_dir = commonpath.most(paths)
        remote_dir = conn.to_remote_path(local_dir)

        # built temporary local tar-file
        file, local_tmp = tempfile.mkstemp(prefix="scp_")
        os.close(file)

        try:
            remote_tmp = "/tmp/" + os.path.basename(local_tmp)

            # pack remote files into a tar archive
            sublime.status_message("SCP: preparing download ...")
            conn.plink("tar -C {0} -cf {1} .".format(remote_dir, remote_tmp))

            def progress(filename, progress):
                sublime.status_message(
                    "SCP: downloading [{}%] ...".format(progress))

            # download tar archive
            super(conn.__class__, conn).getfile(remote_tmp, local_tmp, progress)

            sublime.status_message("SCP: extracting ...")
            with tarfile.open(local_tmp, "r") as tar:
                os.makedirs(local_dir, exist_ok=True)
                os.chdir(local_dir)
                tar.extractall()
            sublime.status_message("SCP: Downloaded %s!" % local_dir)

        except SCPCommandError as err:
            print(str(err).strip())
            sublime.status_message("SCP: Failed to download %s!" % local_dir)

        finally:
            try:
                # delete remote tar archive
                super(conn.__class__, conn).remove(remote_tmp)
            except:
                pass
            try:
                # remove local tar archive
                os.remove(local_tmp)
            except:
                pass


class ScpPutCommand(_ScpWindowCommand):

    def executor(self, task, paths):
        groups = {}
        for path in paths:
            if any(f in path for f in ('.scp', '.git')):
                continue
            try:
                conn = scpfolder.connection(path)
                groups.setdefault(conn, []).append(path)
            except SCPNotConnectedError:
                pass

        for conn, paths in groups.items():
            if len(paths) == 1 and os.path.isfile(paths[0]):
                # use simple upload for single files
                conn.putfile(paths[0])
                msg = "SCP: Uploaded %s!" % paths[0]
                sublime.status_message(msg)
            else:
                # use tarfile upload for multiple files and dirs
                self.puttree(conn, paths)

    def puttree(self, conn, paths):
        """
        Put several folders and files to the remote host.

        Uploading many files via scp is horribly slow. To work around that
        the following steps are performed:
        1. Pack all files given via `paths` into a single tar-file with
           relative paths based on the mapped folder.
        2. Upload the tar-file to the remote's /tmp/ folder.
        3. Untar the file on the remote host and delete it.
        """
        local_dir = commonpath.most(paths)
        remote_dir = conn.to_remote_path(local_dir)

        # built temporary local tar-file
        file, local_tmp = tempfile.mkstemp(prefix="scp_")
        os.close(file)

        sublime.status_message("SCP: preparing upload ...")
        with tarfile.open(local_tmp, "w") as tar:

            def tarfilter(tarinfo):
                for f in ('.scp', '.git'):
                    if f in tarinfo.path:
                        return None
                tarinfo.uid = tarinfo.gid = 0
                tarinfo.uname = tarinfo.gname = "root"
                return tarinfo

            for path in paths:
                tar.add(
                    path,
                    arcname=os.path.relpath(path, local_dir),
                    filter=tarfilter
                )

        try:
            def progress(filename, progress):
                sublime.status_message(
                    "SCP: uploading tarfile [{}%] ...".format(progress))

            # upload using pscp
            remote_tmp = "/tmp/" + os.path.basename(local_tmp)
            super(conn.__class__, conn).putfile(local_tmp, remote_tmp, progress)

            # untar on remote host and delete temporary archive
            sublime.status_message("SCP: extracting uploaded tarfile ...")
            conn.plink("mkdir -p {0}; tar -C {0} -xf {1}; rm {1}".format(remote_dir, remote_tmp))

            msg = "SCP: Uploaded %s!" % local_dir
            sublime.status_message(msg)

        except SCPCommandError as err:
            print(str(err).strip())
            sublime.status_message("SCP: Failed to upload %s!" % local_dir)

        finally:
            # remove local archive
            os.remove(local_tmp)


class ScpDelCommand(_ScpWindowCommand):

    def executor(self, task, paths):
        for path in paths:
            try:
                scpfolder.connection(path).remove(path)
                sublime.status_message("SCP: Deleted %s!" % path)
            except SCPNotConnectedError:
                pass
            except SCPCommandError as err:
                print(str(err).strip())
                sublime.status_message("SCP: Could not delete %s!" % path)


class ScpEventListener(sublime_plugin.EventListener):

    def on_post_save(self, view):
        view.window().run_command("scp_put")
