import os
import re
import subprocess
import sys

import sublime
import sublime_plugin

from .scpclient import SCPClient

connections = []


class ScpNotConnectedError(Exception):
    pass

class ScpAbortError(Exception):
    pass


def connect(path):
    try:
        return connection(path)
    except ScpNotConnectedError:
        try:
            if sys.platform == "win32":
                client = SCPFolder(path.lower())
            else:
                client = SCPFolder(path)
            connections.append(client)
            return client
        except:
            return False


def disconnect(path):
    try:
        while True:
            connections.remove(connection(path))
    except (IndexError, ScpNotConnectedError):
        pass


def connection(path):
    if path:
        p = path.lower() if sys.platform == "win32" else path
        for client in connections:
            if p.startswith(client.root):
                return client
    raise ScpNotConnectedError("No SCP connection for %s!" % path)


def is_connected(path):
    if path:
        p = path.lower() if sys.platform == "win32" else path
        for client in connections:
            if p.startswith(client.root):
                return True
    return False


def root_dir(file_name):
    if file_name:
        path, name = file_name, "."
        while path and name and name != ".scp":
            if path and os.path.exists(os.path.join(path, ".scp")):
                return path
            path, name = os.path.split(path)
    return False


class SCPFolder(SCPClient):

    def __init__(self, path):
        self.root = root_dir(path)
        if not self.root:
            raise ValueError("Not within a mapped folder")
        with open(os.path.join(self.root, ".scp")) as file:
            client = sublime.decode_value(file.read())
            SCPClient.__init__(
                self,
                client["host"],
                client.get("port", 22),
                client.get("user", "guest"),
                client.get("passwd", None)
            )
            self.remote_path = client.get("path", "/")

    def to_remote_path(self, path):
        if path == self.root:
            return self.remote_path
        remote_path = os.path.join(
            self.remote_path, os.path.relpath(path, self.root)
        ).replace("\\", "/")
        if ".." in remote_path:
            raise ValueError("Invalid path!")
        return remote_path

    def to_remote_parent(self, path):
        if path == self.root:
            return os.path.dirname(self.remote_path).replace("\\", "/")
        return self.to_remote_path(os.path.dirname(path))

    def cancel(self):
        super().cancel()
        raise ScpAbortError("Aborted by user!")

    def remove(self, path):
        super().remove(self.to_remote_path(path))

    def mkdir(self, path):
        super().mkdir(self.to_remote_path(path))

    def lsdir(self, path):
        super().lsdir(self.to_remote_path(path))

    def putfile(self, path):
        super().putfile(path, self.to_remote_path(path))

    def getfile(self, path):
        super().getfile(self.to_remote_path(path), path)

    def putdir(self, path):
        super().putdir(path, self.to_remote_parent(path))

    def getdir(self, path):
        super().getdir(self.to_remote_path(path), os.path.dirname(path))
