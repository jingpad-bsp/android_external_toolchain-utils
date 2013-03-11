#!/usr/bin/python
#
# Copyright 2012 Google Inc. All Rights Reserved.

"""Script to use remote try-bot build image with local gcc."""

import argparse
import glob
import os
import re
import shutil
import socket
import sys
import tempfile
import time

from utils import command_executer
from utils import logger
from utils import misc

BRANCH = "the_actual_branch_used_in_this_script"
TMP_BRANCH = "tmp_branch"
SLEEP_TIME = 600
checkout_branch = "toolchain-3428.65.B"


def GetPatchNum(output):
  lines = output.splitlines()
  line = [l for l in lines if "gerrit" in l][0]
  patch_num = re.findall(r"\d+", line)[0]
  if "gerrit-int" in line:
    patch_num = "*" + patch_num
  return str(patch_num)


def GetPatchString(patch):
  if patch:
    return "+".join(patch)
  return "NO_PATCH"


def FindResultIndex(reason):
  """Find the build id of the build at trybot server."""
  running_time = 0
  while True:
    num = GetBuildNumber(reason)
    if num >= 0:
      return num
    logger.GetLogger().LogOutput("{0} minutes passed."
                                 .format(running_time / 60))
    logger.GetLogger().LogOutput("Sleeping {0} seconds.".format(SLEEP_TIME))
    time.sleep(SLEEP_TIME)
    running_time += SLEEP_TIME


def GetBuildNumber(reason):
  """Get the build num from build log."""
  # returns 0 if only failed build found.
  # returns -1 if no finished build found or one build is running.
  # returns build number is successful build found.

  file_dir = os.path.dirname(os.path.realpath(__file__))
  commands = ("{0}/utils/buildbot_json.py builds "
              "http://chromegw/p/tryserver.chromiumos/"
              .format(file_dir))
  ce = command_executer.GetCommandExecuter()
  _, buildinfo, _ = ce.RunCommand(commands, return_output=True,
                                  print_to_console=False)

  my_info = buildinfo.splitlines()
  current_line = 1
  running_job = False
  number = -1
  # number > 0, we have a successful build.
  # number = 0, we have a failed build.
  # number = -1, we do not have a finished build for this description.
  while current_line < len(my_info):
    my_dict = {}
    while True:
      key = my_info[current_line].split(":")[0].strip()
      value = my_info[current_line].split(":", 1)[1].strip()
      my_dict[key] = value
      current_line += 1
      if "Build" in key or current_line == len(my_info):
        break
    if ("True" not in my_dict["completed"] and
        str(reason) in my_dict["reason"]):
      running_job = True
    if ("True" not in my_dict["completed"] or
        str(reason) not in my_dict["reason"]):
      continue
    if my_dict["result"] == "0":
      number = int(my_dict["number"])
      return number
    else:
     # Record found a finished failed build.
     # Keep searching to find a successful one.
      number = 0
  if number == 0 and not running_job:
    return 0
  return -1


def DownloadImage(target, index, dest, version):
  """Download artifacts from cloud."""
  match = re.search(r"(.*-\d+\.\d+\.\d+)", version)
  version = match.group(0)
  if not os.path.exists(dest):
    os.makedirs(dest)

  ls_cmd = ("gsutil ls gs://chromeos-image-archive/trybot-{0}/{1}-b{2}"
            .format(target, version, index))

  download_cmd = ("$(which gsutil) cp {0} {1}".format("{0}", dest))
  ce = command_executer.GetCommandExecuter()

  _, out, _ = ce.RunCommand(ls_cmd, return_output=True, print_to_console=True)
  lines = out.splitlines()
  download_files = ["autotest.tar", "chromeos-chrome",
                    "chromiumos_test_image", "debug.tgz",
                    "sysroot_chromeos-base_chromeos-chrome.tar.xz"
                   ]
  for line in lines:
    if any([e in line for e in download_files]):
      cmd = download_cmd.format(line)
      if ce.RunCommand(cmd):
        logger.GetLogger().LogFatal("Command {0} failed, existing..."
                                    .format(cmd))


def UnpackImage(dest):
  """Unpack the image, the chroot build dir."""
  chrome_tbz2 = glob.glob(dest+"/*.tbz2")[0]
  commands = ("tar xJf {0}/sysroot_chromeos-base_chromeos-chrome.tar.xz "
              "-C {0} &&"
              "tar xjf {1} -C {0} &&"
              "tar xzf {0}/debug.tgz  -C {0}/usr/lib/ &&"
              "tar xf {0}/autotest.tar -C {0}/usr/local/ &&"
              "tar xJf {0}/chromiumos_test_image.tar.xz -C {0}"
              .format(dest, chrome_tbz2))
  ce = command_executer.GetCommandExecuter()
  return ce.RunCommand(commands)


def GetManifest(version, to_file):
  """Get the manifest file from a given chromeos-internal version."""
  temp_dir = tempfile.mkdtemp()
  version = version.split("-", 1)[1]
  os.chdir(temp_dir)
  command = ("git clone "
             "ssh://gerrit-int.chromium.org:29419/"
             "chromeos/manifest-versions.git")
  ce = command_executer.GetCommandExecuter()
  ce.RunCommand(command)
  files = [os.path.join(r, f)
           for r, _, fs in os.walk(".")
           for f in fs if version in f]
  if files:
    command = "cp {0} {1} && rm -rf {2}".format(files[0], to_file, temp_dir)
    ret = ce.RunCommand(command)
    if ret:
      raise Exception("Cannot copy manifest to {0}".format(to_file))
  else:
    command = "rm -rf {0}".format(temp_dir)
    ce.RunCommand(command)
    raise Exception("Version {0} is not available.".format(version))


def RemoveOldBranch():
  """Remove the branch with name BRANCH."""
  ce = command_executer.GetCommandExecuter()
  command = "git rev-parse --abbrev-ref HEAD"
  _, out, _ = ce.RunCommand(command, return_output=True)
  if BRANCH in out:
    command = "git checkout -B {0}".format(TMP_BRANCH)
    ce.RunCommand(command)
  command = "git commit -m 'nouse'"
  ce.RunCommand(command)
  command = "git branch -D {0}".format(BRANCH)
  ce.RunCommand(command)


def UploadManifest(manifest, chromeos_root, branch="master"):
  """Copy the manifest to $chromeos_root/manifest-internal and upload."""
  chromeos_root = misc.CanonicalizePath(chromeos_root)
  manifest_dir = os.path.join(chromeos_root, "manifest-internal")
  os.chdir(manifest_dir)
  ce = command_executer.GetCommandExecuter()

  RemoveOldBranch()

  if branch != "master":
    branch = "{0}".format(branch)
  command = "git checkout -b {0} -t cros-internal/{1}".format(BRANCH, branch)
  ret = ce.RunCommand(command)
  if ret:
    raise Exception("Command {0} failed".format(command))

  # We remove the default.xml, which is the symbolic link of full.xml.
  # After that, we copy our xml file to default.xml.
  # We did this because the full.xml might be updated during the
  # run of the script.
  os.remove(os.path.join(manifest_dir, "default.xml"))
  shutil.copyfile(manifest, os.path.join(manifest_dir, "default.xml"))
  return UploadPatch(manifest)


def GetManifestPatch(version, chromeos_root, branch="master"):
  """Return a gerrit patch number given a version of manifest file."""
  temp_dir = tempfile.mkdtemp()
  to_file = os.path.join(temp_dir, "default.xml")
  GetManifest(version, to_file)
  return UploadManifest(to_file, chromeos_root, branch)


def UploadPatch(source):
  """Up load patch to gerrit, return patch number."""
  commands = ("git add -A . &&"
              "git commit -m 'test' -m 'BUG=None' -m 'TEST=None' "
              "-m 'hostname={0}' -m 'source={1}'"
              .format(socket.gethostname(), source))
  ce = command_executer.GetCommandExecuter()
  ce.RunCommand(commands)

  commands = ("yes | repo upload .   --cbr --no-verify")
  _, _, err = ce.RunCommand(commands, return_output=True)
  return GetPatchNum(err)


def ReplaceSysroot(chromeos_root, dest_dir, target, version):
  """Copy unpacked sysroot and image to chromeos_root."""
  ce = command_executer.GetCommandExecuter()
  board = target.split("-")[0]
  board_dir = os.path.join(chromeos_root, "chroot", "build", board)
  command = "sudo rm -rf {0}".format(board_dir)
  ce.RunCommand(command)

  command = "sudo mv {0} {1}".format(dest_dir, board_dir)
  ce.RunCommand(command)

  image_dir = os.path.join(chromeos_root, "src", "build", "images",
                           board, "latest")
  command = "rm -rf {0} && mkdir -p {0}".format(image_dir)
  ce.RunCommand(command)

  command = "mv {0}/chromiumos_test_image.bin {1}".format(board_dir, image_dir)
  return ce.RunCommand(command)


def GccBranchForToolchain(branch):
  if branch == "toolchain-3428.65.B":
    return "release-R25-3428.B"
  else:
    return None


def GetGccBranch(branch):
  """Get the remote branch name from branch or version."""
  ce = command_executer.GetCommandExecuter()
  command = "git branch -a | grep {0}".format(branch)
  _, out, _ = ce.RunCommand(command, return_output=True)
  if not out:
    release_num = re.match(r".*(R\d+)-*", branch)
    if release_num:
      release_num = release_num.group(0)
      command = "git branch -a | grep {0}".format(release_num)
      _, out, _ = ce.RunCommand(command, return_output=True)
      if not out:
        GccBranchForToolchain(branch)
  if not out:
    logger.GetLogger.LogFatal("The branch/version ${0} "
                              "is not a valid one".format(branch))
  new_branch = out.splitlines()[0]
  return new_branch


def UploadGccPatch(chromeos_root, gcc_dir, branch):
  """Upload local gcc to gerrit and get the CL number."""
  ce = command_executer.GetCommandExecuter()
  gcc_dir = misc.CanonicalizePath(gcc_dir)
  gcc_path = os.path.join(chromeos_root, "src/third_party/gcc")
  assert os.path.isdir(gcc_path), ("{0} is not a valid chromeos root"
                                   .format(chromeos_root))
  assert os.path.isdir(gcc_dir), ("{0} is not a valid dir for gcc"
                                  "source".format(gcc_dir))
  os.chdir(gcc_path)
  RemoveOldBranch()

  if not branch:
    branch = "master"
  branch = GetGccBranch(branch)
  command = ("git checkout -b {0} -t remotes/cros/{1} && "
             "rm -rf *".format(BRANCH, branch))
  ce.RunCommand(command, print_to_console=False)

  command = ("rsync -az --exclude='*.svn' --exclude='*.git'"
             " {0}/ .".format(gcc_dir))
  ce.RunCommand(command)
  return UploadPatch(gcc_dir)


def RunRemote(chromeos_root, branch, patches, is_local,
              target, chrome_version, dest_dir):
  """The actual running commands."""
  ce = command_executer.GetCommandExecuter()

  if is_local:
    local_flag = "--local -r {0}".format(dest_dir)
  else:
    local_flag = "--remote"
  patch = ""
  for p in patches:
    patch += " -g {0}".format(p)
  cbuildbot_path = os.path.join(chromeos_root, "chromite/buildbot")
  os.chdir(cbuildbot_path)
  branch_flag = ""
  if branch != "master":
    branch_flag = " -b {0}".format(branch)
  chrome_version_flag = ""
  if chrome_version:
    chrome_version_flag = " --chrome_version={0}".format(chrome_version)
  description = "{0}_{1}_{2}".format(branch, GetPatchString(patches), target)
  command = ("yes | ./cbuildbot {0} {1} {2} {3} {4} {5}"
             " --remote-description={6}"
             .format(patch, branch_flag, chrome_version, local_flag,
                     chrome_version_flag, target, description))
  ce.RunCommand(command)
  return description


def Main(argv):
  """The main function."""
  # Common initializations
  parser = argparse.ArgumentParser()
  parser.add_argument("-c", "--chromeos_root", required=True,
                      dest="chromeos_root", help="The chromeos_root")
  parser.add_argument("-g", "--gcc_dir", default="", dest="gcc_dir",
                      help="The gcc dir")
  parser.add_argument("-t", "--target", required=True, dest="target",
                      help=("The target to be build, the list is at"
                            " $(chromeos_root)/chromite/buildbot/cbuildbot"
                            " --list -all"))
  parser.add_argument("-l", "--local", action="store_true")
  parser.add_argument("-d", "--dest_dir", dest="dest_dir",
                      help=("The dir to build the whole chromeos if"
                            " --local is set"))
  parser.add_argument("--chrome_version", dest="chrome_version",
                      default="", help="The chrome version to use. "
                      "Default it will use the latest one.")
  parser.add_argument("--chromeos_version", dest="chromeos_version",
                      default="", help="The chromeos version to use.")

  parser.add_argument("-r", "--replace_sysroot", action="store_true",
                      help=("Whether or not to replace the build/$board dir"
                            "under the chroot of chromeos_root and copy "
                            "the image to src/build/image/$board/latest."
                            " Default is False"))
  parser.add_argument("-b", "--branch", dest="branch", default="master",
                      help=("The branch to run trybot, default is master"))
  parser.add_argument("-p", "--patch", dest="patch", default="",
                      help=("The patches to be applied, the patches numbers "
                            "be seperated by ','"))

  script_dir = os.path.dirname(os.path.realpath(__file__))

  args = parser.parse_args(argv[1:])
  target = args.target
  patch = args.patch.split()
  chromeos_root = misc.CanonicalizePath(args.chromeos_root)
  branch = args.branch
  # descritption is the keyword of the build in build log.
  # Here we use [{branch)_{patchnumber}_{target}]
  description = "{0}_{1}_{2}".format(branch, GetPatchString(patch), target)
  if args.chromeos_version and args.branch:
    raise Exception("You can not set chromeos_version and branch at the "
                    "same time.")
  chromeos_version = args.chromeos_version
  if args.branch:
    chromeos_version = 0
  else:
    chromeos_version = args.chromeos_version
  if args.chromeos_version:
    manifest_patch = GetManifestPatch(args.chromeos_version,
                                      chromeos_root)
    patch.append(manifest_patch)
  if args.gcc_dir:
    if not branch:
      branch = chromeos_version
    patch.append(UploadGccPatch(chromeos_root, args.gcc_dir, branch))
  index = 0
  description = RunRemote(chromeos_root, branch, patch, args.local,
                          target, args.chrome_version, args.dest_dir)
  if args.local or not args.dest_dir:
    return 0
  os.chdir(script_dir)
  dest_dir = misc.CanonicalizePath(args.dest_dir)
  if index <= 0:
    index = FindResultIndex(description)
  if not index:
    logger.GetLogger().LogFatal("Remote trybot failed.")
  if branch == checkout_branch:
    chromeos_version = "R25-3428.65.1"
  DownloadImage(target, index, dest_dir, chromeos_version)
  ret = UnpackImage(dest_dir)
  if not args.replace_sysroot:
    return ret
  else:
    return ReplaceSysroot(chromeos_root, args.dest_dir, target,
                          chromeos_version)

if __name__ == "__main__":
  retval = Main(sys.argv)
  sys.exit(retval)

