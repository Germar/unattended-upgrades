#!/usr/bin/python2.4

import apt_pkg, apt_inst
import sys, os, string, datetime
from optparse import OptionParser

import warnings
warnings.filterwarnings("ignore", "apt API not stable yet", FutureWarning)
import apt
import logging

class MyCache(apt.Cache):
    def __init__(self):
        apt.Cache.__init__(self)
    def clear(self):
        for pkg in cache:
            pkg.markKeep()
        assert self._depcache.InstCount == 0 and \
               self._depcache.BrokenCount == 0 \
               and self._depcache.DelCount == 0
        

def is_allowed_origin(pkg, allowed_origins):
    origin = pkg.candidateOrigin
    #print origin
    for allowed in allowed_origins:
        if origin.origin == allowed[0] and origin.archive == allowed[1]:
            return True
    return False

def check_changes_for_sanity(cache, allowed_origins, blacklist):
    if cache._depcache.BrokenCount != 0:
        return False
    for pkg in cache:
        if pkg.markedDelete:
            return False
        if pkg.markedInstall or pkg.markedUpgrade:
            if not is_allowed_origin(pkg, allowed_origins):
                return False
            if pkg.name in blacklist:
                return False
    return True

def pkgname_from_deb(debfile):
    # FIXME: add error checking here
    control = apt_inst.debExtractControl(open(debfile))
    sections = apt_pkg.ParseSection(control)
    return sections["Package"]

def conffile_prompt(destFile):
    logging.debug("check_conffile_prompt('%s')" % destFile)
    pkgname = pkgname_from_deb(destFile)
    status_file = apt_pkg.Config.Find("Dir::State::status")
    parse = apt_pkg.ParseTagFile(open(status_file,"r"))
    while parse.Step() == 1:
        if parse.Section.get("Package") == pkgname:
            logging.debug("found pkg: %s" % pkgname)
            if parse.Section.has_key("Conffiles"):
                conffiles = parse.Section.get("Conffiles")
                # Conffiles:
                # /etc/bash_completion.d/m-a c7780fab6b14d75ca54e11e992a6c11c
                for line in string.split(conffiles,"\n"):
                    logging.debug("conffile line: %s", line)
                    l = string.split(string.strip(line))
                    file = l[0]
                    md5 = l[1]
                    if len(l) > 2:
                        obs = l[2]
                    else:
                        obs = None
                    if os.path.exists(file) and obs != "obsolete":
                        current_md5 = apt_pkg.md5sum(open(file).read())
                        if current_md5 != md5:
                            return True
    return False


if __name__ == "__main__":

    # init the logging
    logdir = apt_pkg.Config.FindDir("APT::UnattendedUpgrades::LogDir",
                                    "/var/log/unattended-upgrades/")
    logfile = logdir+apt_pkg.Config.Find("APT::UnattendedUpgrades::LogFile",
                                         "unattended-upgrades.log")
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s',
                        filename=logfile,
                        filemode='w')

    # init the options
    parser = OptionParser()
    parser.add_option("-d", "--debug",
                      action="store_true", dest="debug", default=False,
                      help="print debug messages")
    (options, args) = parser.parse_args()
    if options.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        pass

    #dldir = "/tmp/pyapt-test"
    #try:
    #    os.mkdir(dldir)
    #    os.mkdir(dldir+"/partial")
    #except OSError:
    #    pass
    #apt_pkg.Config.Set("Dir::Cache::archives",dldir)


    # FIXME: figure with with lsb_release
    # and/or a apt.Config var
    allowed_origins = [("Ubuntu","breezy-security"),
                       ("Ubuntu","dapper")
                       ]
    # pkgs that are (for some reason) not save to install
    blacklisted_pkgs = []

    logging.info("Starting unattended upgrades script")
    
    # get a cache
    cache = MyCache()

    # find out about the packages that are upgradable (in a allowed_origin)
    pkgs_to_upgrade = []
    for pkg in cache:
        if options.debug and pkg.isUpgradable:
            logging.debug("Checking: %s (%s)" % (pkg.name,pkg.candidateOrigin.archive))
        if pkg.isUpgradable and \
               is_allowed_origin(pkg,allowed_origins):
            try:
                pkg.markUpgrade()
                if check_changes_for_sanity(cache, allowed_origins,
                                            blacklisted_pkgs):
                    pkgs_to_upgrade.append(pkg)
            except SystemError:
                # can't upgrade
                pass
            else:
                cache.clear()
                for pkg2 in pkgs_to_upgrade:
                    pkg2.markUpgrade()

    pkgs = "\n".join([pkg.name for pkg in pkgs_to_upgrade])
    logging.debug("pkgs that look like they should be upgraded: %s" % pkgs)
           
    # download what looks good
    if options.debug:
        fetcher = apt_pkg.GetAcquire(apt.progress.TextFetchProgress())
    else:
        fetcher = apt_pkg.GetAcquire()
    list = apt_pkg.GetPkgSourceList()
    list.ReadMainList()
    recs = cache._records
    pm = apt_pkg.GetPackageManager(cache._depcache)
    pm.GetArchives(fetcher,list,recs)
    res = fetcher.Run()

    # now check the downloaded debs for conffile conflicts and build
    # a blacklist
    for item in fetcher.Items:
        logging.debug("%s" % item)
        if item.Status == item.StatError:
            print "A error ocured: '%s'" % item.ErrorText
        if item.Complete == False:
            print "The URI '%s' failed to download, aborting" % item.DescURI
            sys.exit(1)
        if item.IsTrusted == False:
            blacklisted_pkgs.append(pkgname_from_deb(item.DestFile))
        if conffile_prompt(item.DestFile):
            # FIXME: skip package (means to re-run the whole marking again
            # and making sure that the package will not be pulled in by
            # some other package again!
            logging.debug("pkg '%s' has conffile prompt" % pkgname_from_deb(item.DestFile))
            blacklisted_pkgs.append(pkgname_from_deb(item.DestFile))


    # redo the selection about the packages to upgrade based on the new
    # blacklist
    logging.debug("blacklist: %s" % blacklisted_pkgs)
    # find out about the packages that are upgradable (in a allowed_origin)
    if len(blacklisted_pkgs) > 0:
        cache.clear()
        old_pkgs_to_upgrade = pkgs_to_upgrade[:]
        pkgs_to_upgrade = []
        for pkg in old_pkgs_to_upgrade:
            logging.debug("Checking (blacklist): %s" % (pkg.name))
            pkg.markUpgrade()
            if check_changes_for_sanity(cache, allowed_origins,
                                        blacklisted_pkgs):
                 pkgs_to_upgrade.append(pkg)
            else:
                logging.info("package '%s' not upgraded" % pkg.name)
                cache.clear()
                for pkg2 in pkgs_to_upgrade:
                    pkg2.markUpgrade()

    logging.debug("InstCount=%i DelCount=%i BrokenCout=%i" % (cache._depcache.InstCount, cache._depcache.DelCount, cache._depcache.BrokenCount))

    # check what we have
    if len(pkgs_to_upgrade) == 0:
        logging.info("No packages found that can be upgraded unattended")
        sys.exit(0)    

    # do the install based on the new list of pkgs
    pkgs = " ".join([pkg.name for pkg in pkgs_to_upgrade])
    logging.info("Packages that are upgraded: %s" % pkgs)

    # set debconf to NON_INTERACTIVE, redirect output
    os.putenv("DEBIAN_FRONTEND","noninteractive");
    os.putenv("APT_LISTCHANGES_FRONTEND","none");
    
    # redirect to log
    REDIRECT_INPUT = os.devnull
    fd = os.open(REDIRECT_INPUT, os.O_RDWR)
    os.dup2(fd,0)

    now = datetime.datetime.now()
    logfile_dpkg = logdir+'unattended-upgrades-dpkg_%s.log' % now.isoformat('_')
    logging.info("Writing dpkg log to '%s'" % logfile_dpkg)
    fd = os.open(logfile_dpkg,os.O_RDWR|os.O_CREAT)
    os.dup2(fd,1)
    os.dup2(fd,2)

    # create a new package-manager. the blacklist may have changed
    # the markings in the depcache
    if options.debug:
        apt_pkg.Config.Set("Debug::pkgDPkgPM","1")
    #apt_pkg.Config.Set("Debug::pkgDPkgPM","1")    
    pm = apt_pkg.GetPackageManager(cache._depcache)
    pm.GetArchives(fetcher,list,recs)
    try:
        res = pm.DoInstall()
    except SystemError,e:
        logging.error("Installing the upgrades failed!")
        logging.error("error message: '%s'" % e)
        res = False
                
    if res == False:
        logging.error("dpkg returned a error! See '%s' for details" % logfile_dpkg)
    else:
        logging.info("All upgrades installed")

