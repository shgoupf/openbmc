# Recipe creation tool - create command build system handlers
#
# Copyright (C) 2014 Intel Corporation
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import re
import logging
from recipetool.create import RecipeHandler, read_pkgconfig_provides

logger = logging.getLogger('recipetool')

tinfoil = None

def tinfoil_init(instance):
    global tinfoil
    tinfoil = instance

class CmakeRecipeHandler(RecipeHandler):
    def process(self, srctree, classes, lines_before, lines_after, handled):
        if 'buildsystem' in handled:
            return False

        if RecipeHandler.checkfiles(srctree, ['CMakeLists.txt']):
            classes.append('cmake')
            lines_after.append('# Specify any options you want to pass to cmake using EXTRA_OECMAKE:')
            lines_after.append('EXTRA_OECMAKE = ""')
            lines_after.append('')
            handled.append('buildsystem')
            return True
        return False

class SconsRecipeHandler(RecipeHandler):
    def process(self, srctree, classes, lines_before, lines_after, handled):
        if 'buildsystem' in handled:
            return False

        if RecipeHandler.checkfiles(srctree, ['SConstruct', 'Sconstruct', 'sconstruct']):
            classes.append('scons')
            lines_after.append('# Specify any options you want to pass to scons using EXTRA_OESCONS:')
            lines_after.append('EXTRA_OESCONS = ""')
            lines_after.append('')
            handled.append('buildsystem')
            return True
        return False

class QmakeRecipeHandler(RecipeHandler):
    def process(self, srctree, classes, lines_before, lines_after, handled):
        if 'buildsystem' in handled:
            return False

        if RecipeHandler.checkfiles(srctree, ['*.pro']):
            classes.append('qmake2')
            handled.append('buildsystem')
            return True
        return False

class AutotoolsRecipeHandler(RecipeHandler):
    def process(self, srctree, classes, lines_before, lines_after, handled):
        if 'buildsystem' in handled:
            return False

        autoconf = False
        if RecipeHandler.checkfiles(srctree, ['configure.ac', 'configure.in']):
            autoconf = True
            values = AutotoolsRecipeHandler.extract_autotools_deps(lines_before, srctree)
            classes.extend(values.pop('inherit', '').split())
            for var, value in values.iteritems():
                lines_before.append('%s = "%s"' % (var, value))
        else:
            conffile = RecipeHandler.checkfiles(srctree, ['configure'])
            if conffile:
                # Check if this is just a pre-generated autoconf configure script
                with open(conffile[0], 'r') as f:
                    for i in range(1, 10):
                        if 'Generated by GNU Autoconf' in f.readline():
                            autoconf = True
                            break

        if autoconf:
            lines_before.append('# NOTE: if this software is not capable of being built in a separate build directory')
            lines_before.append('# from the source, you should replace autotools with autotools-brokensep in the')
            lines_before.append('# inherit line')
            classes.append('autotools')
            lines_after.append('# Specify any options you want to pass to the configure script using EXTRA_OECONF:')
            lines_after.append('EXTRA_OECONF = ""')
            lines_after.append('')
            handled.append('buildsystem')
            return True

        return False

    @staticmethod
    def extract_autotools_deps(outlines, srctree, acfile=None):
        import shlex
        import oe.package

        values = {}
        inherits = []

        # FIXME this mapping is very thin
        progmap = {'flex': 'flex-native',
                'bison': 'bison-native',
                'm4': 'm4-native'}
        progclassmap = {'gconftool-2': 'gconf',
                'pkg-config': 'pkgconfig'}

        ignoredeps = ['gcc-runtime', 'glibc', 'uclibc']

        pkg_re = re.compile('PKG_CHECK_MODULES\(\[?[a-zA-Z0-9]*\]?, \[?([^,\]]*)[),].*')
        lib_re = re.compile('AC_CHECK_LIB\(\[?([a-zA-Z0-9]*)\]?, .*')
        progs_re = re.compile('_PROGS?\(\[?[a-zA-Z0-9]*\]?, \[?([^,\]]*)\]?[),].*')
        dep_re = re.compile('([^ ><=]+)( [<>=]+ [^ ><=]+)?')

        # Build up lib library->package mapping
        shlib_providers = oe.package.read_shlib_providers(tinfoil.config_data)
        libdir = tinfoil.config_data.getVar('libdir', True)
        base_libdir = tinfoil.config_data.getVar('base_libdir', True)
        libpaths = list(set([base_libdir, libdir]))
        libname_re = re.compile('^lib(.+)\.so.*$')
        pkglibmap = {}
        for lib, item in shlib_providers.iteritems():
            for path, pkg in item.iteritems():
                if path in libpaths:
                    res = libname_re.match(lib)
                    if res:
                        libname = res.group(1)
                        if not libname in pkglibmap:
                            pkglibmap[libname] = pkg[0]
                    else:
                        logger.debug('unable to extract library name from %s' % lib)

        # Now turn it into a library->recipe mapping
        recipelibmap = {}
        pkgdata_dir = tinfoil.config_data.getVar('PKGDATA_DIR', True)
        for libname, pkg in pkglibmap.iteritems():
            try:
                with open(os.path.join(pkgdata_dir, 'runtime', pkg)) as f:
                    for line in f:
                        if line.startswith('PN:'):
                            recipelibmap[libname] = line.split(':', 1)[-1].strip()
                            break
            except IOError as ioe:
                if ioe.errno == 2:
                    logger.warn('unable to find a pkgdata file for package %s' % pkg)
                else:
                    raise

        # Since a configure.ac file is essentially a program, this is only ever going to be
        # a hack unfortunately; but it ought to be enough of an approximation
        if acfile:
            srcfiles = [acfile]
        else:
            srcfiles = RecipeHandler.checkfiles(srctree, ['configure.ac', 'configure.in'])
        pcdeps = []
        deps = []
        unmapped = []
        unmappedlibs = []
        with open(srcfiles[0], 'r') as f:
            for line in f:
                if 'PKG_CHECK_MODULES' in line:
                    res = pkg_re.search(line)
                    if res:
                        res = dep_re.findall(res.group(1))
                        if res:
                            pcdeps.extend([x[0] for x in res])
                    inherits.append('pkgconfig')
                if line.lstrip().startswith('AM_GNU_GETTEXT'):
                    inherits.append('gettext')
                elif 'AC_CHECK_PROG' in line or 'AC_PATH_PROG' in line:
                    res = progs_re.search(line)
                    if res:
                        for prog in shlex.split(res.group(1)):
                            prog = prog.split()[0]
                            progclass = progclassmap.get(prog, None)
                            if progclass:
                                inherits.append(progclass)
                            else:
                                progdep = progmap.get(prog, None)
                                if progdep:
                                    deps.append(progdep)
                                else:
                                    if not prog.startswith('$'):
                                        unmapped.append(prog)
                elif 'AC_CHECK_LIB' in line:
                    res = lib_re.search(line)
                    if res:
                        lib = res.group(1)
                        libdep = recipelibmap.get(lib, None)
                        if libdep:
                            deps.append(libdep)
                        else:
                            if libdep is None:
                                if not lib.startswith('$'):
                                    unmappedlibs.append(lib)
                elif 'AC_PATH_X' in line:
                    deps.append('libx11')

        if unmapped:
            outlines.append('# NOTE: the following prog dependencies are unknown, ignoring: %s' % ' '.join(unmapped))

        if unmappedlibs:
            outlines.append('# NOTE: the following library dependencies are unknown, ignoring: %s' % ' '.join(unmappedlibs))
            outlines.append('#       (this is based on recipes that have previously been built and packaged)')

        recipemap = read_pkgconfig_provides(tinfoil.config_data)
        unmapped = []
        for pcdep in pcdeps:
            recipe = recipemap.get(pcdep, None)
            if recipe:
                deps.append(recipe)
            else:
                if not pcdep.startswith('$'):
                    unmapped.append(pcdep)

        deps = set(deps).difference(set(ignoredeps))

        if unmapped:
            outlines.append('# NOTE: unable to map the following pkg-config dependencies: %s' % ' '.join(unmapped))
            outlines.append('#       (this is based on recipes that have previously been built and packaged)')

        if deps:
            values['DEPENDS'] = ' '.join(deps)

        if inherits:
            values['inherit'] = ' '.join(list(set(inherits)))

        return values


class MakefileRecipeHandler(RecipeHandler):
    def process(self, srctree, classes, lines_before, lines_after, handled):
        if 'buildsystem' in handled:
            return False

        makefile = RecipeHandler.checkfiles(srctree, ['Makefile'])
        if makefile:
            lines_after.append('# NOTE: this is a Makefile-only piece of software, so we cannot generate much of the')
            lines_after.append('# recipe automatically - you will need to examine the Makefile yourself and ensure')
            lines_after.append('# that the appropriate arguments are passed in.')
            lines_after.append('')

            scanfile = os.path.join(srctree, 'configure.scan')
            skipscan = False
            try:
                stdout, stderr = bb.process.run('autoscan', cwd=srctree, shell=True)
            except bb.process.ExecutionError as e:
                skipscan = True
            if scanfile and os.path.exists(scanfile):
                values = AutotoolsRecipeHandler.extract_autotools_deps(lines_before, srctree, acfile=scanfile)
                classes.extend(values.pop('inherit', '').split())
                for var, value in values.iteritems():
                    if var == 'DEPENDS':
                        lines_before.append('# NOTE: some of these dependencies may be optional, check the Makefile and/or upstream documentation')
                    lines_before.append('%s = "%s"' % (var, value))
                lines_before.append('')
                for f in ['configure.scan', 'autoscan.log']:
                    fp = os.path.join(srctree, f)
                    if os.path.exists(fp):
                        os.remove(fp)

            self.genfunction(lines_after, 'do_configure', ['# Specify any needed configure commands here'])

            func = []
            func.append('# You will almost certainly need to add additional arguments here')
            func.append('oe_runmake')
            self.genfunction(lines_after, 'do_compile', func)

            installtarget = True
            try:
                stdout, stderr = bb.process.run('make -qn install', cwd=srctree, shell=True)
            except bb.process.ExecutionError as e:
                if e.exitcode != 1:
                    installtarget = False
            func = []
            if installtarget:
                func.append('# This is a guess; additional arguments may be required')
                makeargs = ''
                with open(makefile[0], 'r') as f:
                    for i in range(1, 100):
                        if 'DESTDIR' in f.readline():
                            makeargs += " 'DESTDIR=${D}'"
                            break
                func.append('oe_runmake install%s' % makeargs)
            else:
                func.append('# NOTE: unable to determine what to put here - there is a Makefile but no')
                func.append('# target named "install", so you will need to define this yourself')
            self.genfunction(lines_after, 'do_install', func)

            handled.append('buildsystem')
        else:
            lines_after.append('# NOTE: no Makefile found, unable to determine what needs to be done')
            lines_after.append('')
            self.genfunction(lines_after, 'do_configure', ['# Specify any needed configure commands here'])
            self.genfunction(lines_after, 'do_compile', ['# Specify compilation commands here'])
            self.genfunction(lines_after, 'do_install', ['# Specify install commands here'])


def register_recipe_handlers(handlers):
    # These are in a specific order so that the right one is detected first
    handlers.append(CmakeRecipeHandler())
    handlers.append(AutotoolsRecipeHandler())
    handlers.append(SconsRecipeHandler())
    handlers.append(QmakeRecipeHandler())
    handlers.append(MakefileRecipeHandler())
