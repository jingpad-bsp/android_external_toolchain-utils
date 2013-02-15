#!/usr/bin/python2.6
#
# Copyright 2010 Google Inc. All Rights Reserved.

"""Script to build the ChromeOS toolchain.

This script sets up the toolchain if you give it the gcctools directory.
A typical usage scenerio of this script is as follows:

  update_git_repo.py -t gcc
  update_git_repo.py -t binutils

To set it up for the first time, run the following:
  update_git_repo.py -t gcc -c <CL#>
  update_git_repo.py -t gcc -b <Branch name> -c <CL#>
  update_git_repo.py -t gcc
  update_git_repo.py -t gcc -b <Branch name>
"""

__author__ = "asharif@google.com (Ahmad Sharif)"

import getpass
import optparse
import os
import re
import socket
import sys
import tempfile
from utils import command_executer
from utils import logger
from utils import utils

# Common initializations
cmd_executer = command_executer.GetCommandExecuter()
(rootdir, basename) = utils.GetRoot(sys.argv[0])


def CreateP4Client(client_name, p4_port, p4_paths, checkoutdir):
  """Creates a perforce client from the given parameters."""
  command = "cd " + checkoutdir
  command += "; cp ${HOME}/.p4config ."
  command += "; echo \"P4PORT=" + p4_port + "\" >> .p4config"
  command += "; echo \"P4CLIENT=" + client_name + "\" >> .p4config"
  command += "; g4 client -a " + " -a ".join(p4_paths)
  print command
  retval = cmd_executer.RunCommand(command)
  return retval


def DeleteP4Client(client_name, client_dir):
  command = "cd " + client_dir
  command += "&& g4 client -d " + client_name
  retval = cmd_executer.RunCommand(command)
  return retval


def SyncP4Client(client_name, checkoutdir, revision=None):
  command = "cd " + checkoutdir
  if revision:
    command += " && g4 sync ...@" + revision
  else:
    command += " && g4 sync ..."
  retval = cmd_executer.RunCommand(command)
  return retval


def GetP4PathsForTool(client_name, tool, branch_path):
  """Returns the perforce paths for a tool {gcc|binutils}."""
  p4_paths = []
  if branch_path[-1] == "/":
    branch_path = branch_path[0:-1]
  if tool == "gcc":
    p4_paths.append("\"" + branch_path + "/google_vendor_src_branch/" +
                    "gcc/gcc-4.4.3/..." +
                    " //" + client_name + "/gcc/gcc-4.4.3/..." +
                    "\"")
    version_number = utils.GetRoot(rootdir)[1]
    p4_paths.append("\"//depot2/gcctools/chromeos/" + version_number +
                    "/build-gcc/..." +
                    " //" + client_name + "/build-gcc/..." +
                    "\"")
  elif tool == "binutils":
    p4_paths.append("\"" + branch_path + "/google_vendor_src_branch/" +
                    "binutils/binutils-2.20.1-mobile/..." +
                    " //" + client_name + "/binutils/binutils-2.20.1-mobile/..." +
                    "\"")
    p4_paths.append("\"" + branch_path + "/google_vendor_src_branch/" +
                    "binutils/binutils-20100303/..." +
                    " //" + client_name + "/binutils/binutils-20100303/..." +
                    "\"")
    version_number = utils.GetRoot(rootdir)[1]
    p4_paths.append("\"//depot2/gcctools/chromeos/" + version_number +
                    "/build-binutils/..." +
                    " //" + client_name + "/build-binutils/..." +
                    "\"")

  return p4_paths


def GetGitRepoForTool(tool):
  # This should return the correct server repository when the script
  # is fully done.
  if tool == "gcc":
    return "ssh://git@gitrw.chromium.org:9222/gcc.git"
  elif tool == "binutils":
    return "ssh://git@gitrw.chromium.org:9222/binutils.git"


def SetupBranch(checkoutdir, branch_name):
  """Sets up either the master or another branch in the local repo."""
  if branch_name == "master":
    return 0
  command = "cd " + checkoutdir
  command += " && git branch -a | grep -wq " + branch_name
  retval = cmd_executer.RunCommand(command)
  command = "cd " + checkoutdir
  if retval == 0:
    command += (" && git branch --track " + branch_name +
                " remotes/origin/" + branch_name)
    command += " && git checkout " + branch_name
  else:
    command += (" && git checkout -b " + branch_name)
  retval = cmd_executer.RunCommand(command)
  return retval


def CreateGitClient(git_repo, checkoutdir, branch_name):
  """Creates a git client with in a dir with a branch."""
  command = "cd " + checkoutdir
  command += " && git clone -v " + git_repo + " ."
  retval = cmd_executer.RunCommand(command)
  if retval != 0:
    return retval
  retval = SetupBranch(checkoutdir, branch_name)
  if retval != 0:
    return retval
  command = "cd " + checkoutdir
  command += " && rm -rf *"
  retval = cmd_executer.RunCommand(command)
  return retval


def GetLatestCL(client_name, checkoutdir):
  command = "cd " + checkoutdir
  command += " && g4 changes -m1 ...#have"
  (status, stdout, stderr) = cmd_executer.RunCommand(command, True)
  if status != 0:
    return -1
  mo = re.match("^Change (\d+)", stdout)
  if mo is None:
    return -1
  return mo.groups(0)[0]


def WriteStringToFile(filename, string):
  f = open(filename, "w")
  f.write(string)
  f.close()


def AddGitIgnores(checkoutdir):
  f = open(checkoutdir + "/.gitignore", "w")
  f.write(".gitignore\n")
  f.write("README.google\n")
  f.write(".p4config\n")
  f.close()


def PushToRemoteGitRepo(checkoutdir, branch_name, message_file, push_args):
  """Do the actualy git push to remote repository."""
  # Add the stuff we want git to ignore for the add.
  AddGitIgnores(checkoutdir)
  command = "cd " + checkoutdir
  # For testing purposes, I am only adding a single file to the
  # remote repository.
  command += " && git add -Av . "
###  command += " && git add -Av gcc/gcc-4.4.3/README"
  command += " && git commit -v -F " + message_file
  command += (" && git push -v " + push_args + " origin " +
              branch_name + ":" + branch_name)
  retval = cmd_executer.RunCommand(command)
  return retval


def Main():
  """The main function."""
  parser = optparse.OptionParser()
  parser.add_option("-t", "--tool", dest="tool",
                    help="Tool can be gcc or binutils.")
  parser.add_option("-b", "--branch", dest="branch",
                    help="Full branch path to use, if not the trunk.")
  parser.add_option("-n", "--dry-run", dest="dry_run", default=False,
                    action="store_true",
                    help="Do a dry run of the git push.")
  parser.add_option("-F", "--file", dest="message_file",
                    help="Path to file containing the commit message.")
  parser.add_option("-r", "--remote", dest="remote",
                    help="Optional location of the remote git repository.")
  parser.add_option("-c", "--changelist", dest="changelist",
                    help="Optional changelist to sync to.")

  options = parser.parse_args()[0]

  if options.tool is None:
    parser.print_help()
    sys.exit()

  if not options.branch:
    branch_path = ("//depot2/gcctools/")
    branch_name = "master"
  else:
    branch_path = ("//depot2/branches/" + options.branch +
                   "/gcctools")
    branch_name = options.branch
    utils.AssertTrue(os.path.exists("/google/src/files/p2/head/depot2/" +
                                    "branches/" + branch_name),
                     "Branch name: " + branch_name + " not found!")

  if options.remote:
    git_repo = options.remote
  else:
    git_repo = GetGitRepoForTool(options.tool)

  # Setup a perforce checkout of Crosstool trunk or branch.
  temp_dir = tempfile.mkdtemp()
  client_name = getpass.getuser() + "-" + socket.gethostname() + "-" + temp_dir
  client_name = str.replace(client_name, "/", "-")
  p4_paths = GetP4PathsForTool(client_name, options.tool, branch_path)
  status = CreateGitClient(git_repo, temp_dir, branch_name)
  utils.AssertTrue(status == 0, "Git repo cloning failed")
  status = CreateP4Client(client_name, "perforce2:2666", p4_paths, temp_dir)
  utils.AssertTrue(status == 0, "Could not create p4 client")
  # If the user presses Ctrl-C, make sure to clean up p4 client.
  try:
    status = SyncP4Client(client_name, temp_dir, options.changelist)
    utils.AssertTrue(status == 0, "Could not sync p4 client")
    if not options.message_file:
      changelist = GetLatestCL(client_name, temp_dir)
      message_file = tempfile.mktemp()
      WriteStringToFile(message_file, "Sync'd to revision " + changelist)
    else:
      message_file = rootdir + "/" + options.message_file
    if options.dry_run:
      push_args = " -n "
    else:
      push_args = ""
    status = PushToRemoteGitRepo(temp_dir, branch_name, message_file, push_args)
    utils.AssertTrue(status == 0, "Could not push to remote repo")
  except (KeyboardInterrupt, SystemExit):
    logger.GetLogger().LogOutput("Caught exception... Cleaning up.")
    status = DeleteP4Client(client_name, temp_dir)
    raise
  status = DeleteP4Client(client_name, temp_dir)
  utils.AssertTrue(status == 0, "Could not delete p4 client")
  return status


if __name__ == "__main__":
  retval = Main()
  sys.exit(retval)


