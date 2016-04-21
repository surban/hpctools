import logging
import os
import sys
import glob
import numpy as np
import re
import shutil
import json
from warnings import warn

log = logging.getLogger("gridsearch")


class GridSearchError(Exception):
    pass


class DependentObject(object):
    """
    An object with dependendcy support.
    """

    def __init__(self):
        self._dependants = []
        self._dependants_subtree = None

    def add_dependant(self, dependant):
        """
        Adds a dependant object.
        :param dependant: the dependant object
        """
        if not isinstance(dependant, DependentObject):
            raise TypeError("depandent must be a DependentObject")
        self._dependants.append(dependant)

    def reset_dependants_subtree(self):
        """
        Resets the computed subtree of dependencies.
        Necessary if dependencies are added after dependants_subtree has been read.
        """
        self._dependants_subtree = None

    @property
    def dependants_subtree(self):
        """
        List of objects that dependend on this object, either directly or via other objects.
        """
        if self._dependants_subtree is not None:
            return self._dependants_subtree
        else:
            self._build_dependants_subtree()
            return self._dependants_subtree

    def has_dependant(self, other):
        """
        Returns True if other depends on this object, either directly or via other objects.
        """
        return other in self.dependants_subtree

    def depends_on(self, other):
        """
        Returns True if this object depends on other, either directly or via other objects.
        """
        return other.has_dependant(self)

    def _build_dependants_subtree(self):
        self._dependants_subtree = []
        for dep in self._dependants:
            self._dependants_subtree.append(dep)
            self._dependants_subtree += dep.dependants_subtree


def sort_by_dependencies(dependent_objs):
    """
    Sorts a list of DependentObjects so that in the sorted list an object always comes before any
     object(s) that depend on it.
    :param dependent_objs: a list of DependentObjects to sort
    :type dependent_objs: list[DependentObject]
    :return: list where objects always come before objects they depend on
    :rtype: list[DependentObject]
    """
    for d in dependent_objs:
        if not isinstance(d, DependentObject):
            raise TypeError("dependent_objs must be a list of DependentObjects")

    def compare(a, b):
        if a.has_dependant(b):
            return -1
        elif a.depends_on(b):
            return 1
        else:
            return 0

    return sorted(dependent_objs, cmp=compare)


class Parameter(DependentObject):
    def __init__(self, name, values):
        super(Parameter, self).__init__()
        self.name = name
        self.values = values
        self.only_for = {}

    def should_scan(self, upper_values):
        for pname, values in self.only_for.iteritems():
            if upper_values[pname] not in values:
                return False
        return True

    def __repr__(self):
        s = str(self.values)
        if len(self.only_for.keys()) > 0:
            s += " (only for %s)" % str(self.only_for)
        return s


class GridGroup(object):
    def __init__(self, value, dependent_parameters):
        if not isinstance(dependent_parameters, list):
            raise TypeError("dependent_parameters must be a list of dependent parameters")
        self.value = value
        self.dependent_parameteres = dependent_parameters


class GridSearch(object):
    predefined_parameters = ["CFG_INDEX"]

    def __init__(self, name, template, parameter_ranges, only_for={}):
        self._name = name
        self._template = template
        self._parameters = self._parse_parameters(parameter_ranges, only_for)

        log.debug("parsed parameters: %s" % str(self._parameters))

        self._check_parameters()

    def _parse_parameters(self, para_strs, only_for):
        only_for = only_for.copy()
        parameters = {}
        for p, rng_spec in para_strs.iteritems():
            try:
                if isinstance(rng_spec, basestring):
                    val = self._parse_rng_str(rng_spec)
                else:
                    val = []
                    for e in rng_spec:
                        if isinstance(e, basestring):
                            val.extend(self._parse_rng_str(e))
                        elif isinstance(e, GridGroup):
                            val.append(e.value)
                            for dep in e.dependent_parameteres:
                                if dep not in only_for:
                                    only_for[dep] = {}
                                if p not in only_for[dep]:
                                    only_for[dep][p] = []
                                only_for[dep][p].append(e.value)
                        else:
                            val.append(e)
                pname = p.upper()
                parameters[pname] = Parameter(pname, val)
                if p in only_for:
                    for name, value in only_for[p].iteritems():
                        if not isinstance(value, list):
                            value = [value]
                        parameters[pname].only_for[name] = value
            except ValueError as e:
                log.debug("inner exception:" + str(e))
                raise GridSearchError("could not parse parameter %s: %s" % (p, e.message))

        # add parameter dependencies
        for p, forspec in only_for.iteritems():
            pname = p.upper()
            if pname not in parameters:
                raise GridSearchError("only_for or GridGroup specified for parameter %s without range specification"
                                      % pname)
            for name, value in forspec.iteritems():
                if not isinstance(value, list):
                    value = [value]
                parameters[pname].only_for[name] = value
        for spec in parameters.itervalues():
            for depname in spec.only_for.iterkeys():
                parameters[depname].add_dependant(spec)

        return parameters

    def _parse_value_str(self, value_str):
        values = []
        for rng_str in value_str.split(","):
            values.extend(self._parse_rng_str(rng_str))
        return values

    def _parse_rng_str(self, rng_str):
        if ":" in rng_str:
            rng_parts = rng_str.split(":")
            if len(rng_parts) == 3:
                start = float(rng_parts[0])
                step = float(rng_parts[1])
                end = float(rng_parts[2])
            elif len(rng_parts) == 2:
                start = float(rng_parts[0])
                step = 1
                end = float(rng_parts[1])
            else:
                raise ValueError("range specification %s is not recognized" % rng_str)
            logging.debug("Range string %s parsed as: start=%g step=%g end=%g" %
                          (rng_str, start, step, end))
            return np.arange(start, end + step/100., step)
        else:
            try:
                return [float(rng_str)]
            except ValueError:
                return [rng_str]
            except TypeError:
                return [rng_str]

    def _get_used_parameters(self):
        params = []
        for m in re.finditer(r"\$(\w+)\$", self._template + " " + self._name):
            params.append(m.group(1).upper())
        return set(params)

    def _check_parameters(self):
        used_params = self._get_used_parameters()
        used_params |= set(self.predefined_parameters)
        specified_params = set(self._parameters.keys())
        specified_params |= set(self.predefined_parameters)
        used_but_not_specified = used_params - specified_params
        if used_but_not_specified:
            raise GridSearchError("parameter(s) %s used in template but no range was specified" %
                                  str(list(used_but_not_specified)))
        specified_but_not_used = specified_params - used_params
        if specified_but_not_used:
            warn("parameter(s) %s specified but not used in template" % str(specified_but_not_used))

    def _instantiate(self, template, parameters):
        inst = template
        rpl_tag = "###REPLACEMENT_TAG###"
        for p, val in parameters.iteritems():
            inst = re.sub(r"\$%s\$" % re.escape(p), rpl_tag, inst, flags=re.IGNORECASE)
            inst = inst.replace(rpl_tag, str(val))
        return inst

    def _generate_rec(self, p_rest, upper_vals):
        upper_vals = upper_vals.copy()

        if p_rest:
            p = p_rest[0]
            ps = self._parameters[p]

            if ps.should_scan(upper_vals):
                val_rng = ps.values
            else:
                val_rng = [ps.values[0]]

            for val in val_rng:
                upper_vals[p] = val
                for rest in self._generate_rec(p_rest[1:], upper_vals):
                    p_vals = {p: val}
                    p_vals.update(rest)
                    yield p_vals
        else:
            yield {}

    def generate(self):
        plist = sort_by_dependencies(self._parameters.values())
        pnames = [p.name for p in plist]
        cfg_index = 0

        for p_vals in self._generate_rec(pnames, {}):
            cfg_index += 1
            p_vals["CFG_INDEX"] = "%05d" % cfg_index

            name = self._instantiate(self._name, p_vals)
            data = self._instantiate(self._template, p_vals)

            dirname, filename = os.path.split(name)
            try:
                os.makedirs(dirname)
            except:
                pass
            with open(name, 'w') as f:
                f.write(data)

            name_wihout_ext, _ = os.path.splitext(name)
            with open(name_wihout_ext + ".json", 'w') as f:
                json.dump(p_vals, f, indent=4)


def gridsearch(name, template, parameter_ranges, only_for={}):
    GridSearch(name, template, parameter_ranges, only_for).generate()


def remove_index_dirs():
    """Deletes all subfolders of the current directory whose name is an integer number."""
    for filename in glob.glob("*"):
        if filename == ".." or filename == ".":
            continue
        if os.path.isdir(filename):
            try:
                int(filename)
            except ValueError:
                continue

            for i in range(10):
                try:
                    if sys.platform == 'win32':
                        import win32api, win32con
                        desktopfile = os.path.join(filename, "desktop.ini")
                        if os.path.exists(desktopfile):
                            win32api.SetFileAttributes(desktopfile, win32con.FILE_ATTRIBUTE_NORMAL)
                        win32api.SetFileAttributes(filename, win32con.FILE_ATTRIBUTE_NORMAL)

                    shutil.rmtree(filename)
                    break
                except:
                    pass


