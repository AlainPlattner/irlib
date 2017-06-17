""" Contains the `Survey` class, which is the overarching `irlib` structure.
`Survey` classes handle interaction with the raw HDF datasets, and spawn off
`Gather` classes for analysis. Each HDF dataset can be opened as a `Survey`,
which stores file references and collects metadata in the form of a
`FileHandler`. Radar lines can be created from a `Survey` using the
`ExtractLine` method, which returns a `Gather`. """

from __future__ import print_function

import os
import sys
import h5py
import numpy as np

try:
    import cPickle as pickle
except ImportError:
    import pickle

from .gather import CommonOffsetGather
from .recordlist import RecordList, ParseError
from .autovivification import AutoVivification

class Survey:
    """ Surveys can be broken down into **Gathers** and *traces*. To create a
    survey and extract a gather, do something like::

        # Create the survey
        S = Survey("mysurvey.h5")

        # Create the gather (Line)
        linenumber = 0      # This can be any nonzero integer
        datacapture = 0     # This corresponds to the channel frequency in
                            # dualdar setups
        L = S.ExtractLine(linenumber, dc=datacapture)

        # To see what can be done with `L`, refer to the `CommonOffsetGather
        # documentation
    """

    def __init__(self, datafile):
        """ Instantiate a **Survey** object. A survey encompasses one HDF5 file
        generated from Blue System Inc. IceRadar software.

        Parameters
        ----------
        datafile : file path to a HDF file generated by Blue Systems IceRadar
                   [string]
        """
        self.f = None
        self.datafile = datafile
        if not os.path.isfile(datafile):
            raise IOError("No survey exists at {0}".format(datafile))

        # Create 2-level boolean map of the dataset
        with h5py.File(datafile, mode="r") as f:
            self.retain = AutoVivification()
            for line in f:
                if isinstance(f[line], h5py.Group):
                    for location in list(f):
                        self.retain[line][location] = True
        return

    def __getitem__(self, key):
        if isinstance(key, int):
            return self.ExtractLine(key, datacapture=0)
        elif isinstance(key, tuple):
            if len(key) == 2:
                if isinstance(key[0], int) and isinstance(key[1], int):
                    return self.ExtractLine(key[0], datacapture=key[1])
                else:
                    raise TypeError("indices must be integers")
            else:
                raise ValueError("one or two indices required")
        else:
            raise TypeError("indices must be integers")

    def __repr__(self):
        return "survey object: " + self.datafile

    def _getdatasets(self, line=None):
        """ Return a list of datasets.

        Parameters
        ----------
        line : (optional) specify a line number [integer]
        """
        if isinstance(line, int):
            path = 'line_{0}/'.format(line)
        else:
            path = '/'

        datasets = []
        def filter_datasets(ds):
            if isinstance(f[ds], h5py.Dataset) and ("picked" not in f[path][ds].name):
                datasets.append(ds)

        with h5py.File(self.datafile) as f:
            f[path].visit(filter_datasets)
        return datasets

    def GetLines(self):
        """ Return a list of the lines contained within the survey. """
        with h5py.File(self.datafile) as f:
            lines = [name for name in f.keys() if name[:4] == 'line']
        lines.sort(key=(lambda s: int(s.split('_')[1])))
        return lines

    def GetChannelsInLine(self, lineno):
        """ Return the number of channels (datacaptures per location) in a
        line. If the number is not constant throughout the line, then return
        the maximum number.

        Parameters
        ----------
        lineno : line number [integer]
        """
        try:
            line = self.GetLines()[lineno]
        except IndexError:
            sys.stderr.write("lineno out of range ({0} not in {1}:{2})\n"
                    .format(lineno, 0, len(self.GetLines)))
        with h5py.File(self.datafile) as f:
            if len(f[line].keys()) == 0:
                raise EmptyLineError('empty line')
            n_datacaptures = [len(f[line][loc]) for loc in f[line].keys()]
        return max(n_datacaptures)

    def ExtractTrace(self, line, location, datacapture=0, echogram=0):
        """ Extract the values for a trace and return as a vector.

        Parameters
        ----------
        line : line number [integer]
        location : trace number [integrer]
        datacapture : (default 0) channel number [integer]
        echogram : (default 0) echogram number [integer]
        """
        path = ('line_{lin}/location_{loc}/datacapture_{dc}/'
                'echogram_{eg}'.format(lin=line, loc=location, dc=datacapture,
                                       eg=echogram))
        with h5py.File(self.datafile) as f:
            vec = f[path][:]
        return vec

    def ExtractLine(self, line, bounds=(None,None), datacapture=0,
                    fromcache=False, cache_dir="cache", print_fnm=False,
                    verbose=False, gather_type=CommonOffsetGather):
        """ Extract every trace on a line. If bounds are supplied
        (min, max), limit extraction to only the range specified.
        Return a CommonOffsetGather instance.

        Parameters
        ----------
        line : line number to extract [integer]
        bounds : return a specific data slice [integer x2]
        datacapture : datacapture subset to load [integer]
        fromcache : attempt to load from a cached file [boolean]
        cache_dir : specify a cache directory [str]
        print_fnm : print the cache search path [boolean]
        """

        if fromcache:
            fnm = self.GetLineCacheName(line, dc=datacapture, cache_dir=cache_dir)
            if print_fnm:
                print(fnm)
            if os.path.isfile(fnm):
                with open(fnm, 'r') as f:
                    unpickler = pickle.Unpickler(f)
                    gatherdata = unpickler.load()
                return gatherdata
            else:
                sys.stderr.write("Cached file {0} not available; loading from "
                                 "HDF\n".format(fnm))

        path = 'line_{lin}'.format(lin=line)

        # Separate out all datasets on the correct line
        names = []
        with h5py.File(self.datafile, "r") as f:
            f[path].visit(names.append)

            # Filter out the datasets, then the correct datacaptures
            # The following is a nested filter that first keeps elements of type
            # *h5py.Dataset*, next discards picked data, and finally restricts the
            # names to the datacaptures specified by *datacapture*.
            if hasattr(datacapture, "__iter__"):
                allowed_datacaptures = ["datacapture_{0}".format(dc) for dc in datacapture]
            else:
                allowed_datacaptures = ["datacapture_{0}".format(datacapture)]
            ds_generator = (f[path][name] for name in names)
            datasets = [name for name, ds in zip(names, ds_generator)
                        if isinstance(ds, h5py.Dataset) and
                            "picked" not in ds.name and
                            name.split("/")[-2] in allowed_datacaptures]
            if len(datasets) == 0:
                sys.stderr.write("no datasets match the specified channel(s)\n")

            # Sort the datasets by location number
            datasets.sort(key=(lambda s: int(s.split('/')[0].split('_')[1])))

            # If bounds are specified, slice out the excess locations
            if len(bounds) != 2:
                sys.stderr.write("bounds kwarg in ExtractLine() "
                                 "must be a two element list or tuple\n")
            if bounds[1] != None:
                datasets = datasets[:bounds[1]]
            if bounds[0] != None:
                datasets = datasets[bounds[0]:]

            # Grab XML metadata
            metadata = RecordList(self.datafile)
            for trace in datasets:
                fullpath = path + '/' + trace
                try:
                    metadata.AddDataset(f[path][trace], HDFpath2fid(fullpath))
                except ParseError as e:
                    sys.stderr.write(e.message + '\n')
                    metadata.CropRecords()
                except ValueError:
                    sys.stderr.write("Malformed path: {0}\n".format(fullpath))

            # Create a single numpy array of data
            # Sometimes the number of samples changes within a line. When this
            # happens, pad the short traces with zeros.
            line_ptr = f[path]
            nsamples = [line_ptr[dataset].shape[0] for dataset in datasets]
            try:
                maxsamples = max(nsamples)
                arr = np.zeros((len(datasets), maxsamples))
                for j, dataset in enumerate(datasets):
                    arr[j,:nsamples[j]] = line_ptr[dataset][:]
            except ValueError:
                sys.stderr.write("Failed to index {0} - it might be "
                                 "empty\n".format(path))
                return

        return gather_type(arr.T, infile=self.datafile, line=line,
                metadata=metadata, retain=self.retain['line_{0}'.format(line)],
                dc=datacapture)

    def GetLineCacheName(self, line, dc=0, cache_dir="cache"):
        """ Return a standard cache name.

        Parameters
        ----------
        line : line number [integer]
        dc : datacapture number [integer]
        cache_dir : (default `cache/`) cache directory [string]
        """
        cnm = os.path.join(cache_dir,
                os.path.splitext(os.path.basename(self.datafile))[0] + \
                '_line' + str(line) + '_' + str(dc) + '.ird')
        return cnm

    def WriteHDF5(self, fnm, overwrite=False):
        """ Given a filename, write the contents of the original file to a
        new HDF5 wherever self.retain is True. The usage case for this is
        when bad data have been identified in the original file.

        Note that for now, this does not preserve HDF5 object comments.

        Parameters
        ----------
        fnm : file path [string]
        overwrite : (dafault `False`) overwrite existing file [boolean]
        """
        if os.path.exists(fnm) and not overwrite:
            print('already exists')
            return

        with h5py.File(fnm, 'w') as fout:

            for line in self.f:
                if isinstance(self.f[line], h5py.Group):
                    try:
                        fout.create_group(line)
                    except ValueError:
                        raise Exception("somehow, {0} already existed in "
                                        "Survey.WriteHDF5(). This might be a "
                                        "problem, and you should look into "
                                        "it.". format(line))
                    print("\t{0}".format(line))
                    for location in list(self.f[line]):
                        if self.retain[line][location]:
                            self.f.copy('{0}/{1}'.format(line, location),
                                        fout[line])
        return


def HDFpath2fid(path):
    """ Based on an HDF path, return a unique FID for table
    relations. """
    lin, loc, dc, ec = [int(a.rsplit("_",1)[1]) for a in path[1:].split("/")]
    fid = str(lin).rjust(4,'0') + str(loc).rjust(4,'0') \
        + str(dc).rjust(4,'0') + str(ec).rjust(4,'0')
    return fid


class EmptyLineError(Exception):
    def __init__(self, message="No message"):
        self.message = message
    def __str__(self):
        return self.message
