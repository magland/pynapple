"""        
    The class `IntervalSet` deals with non-overlaping epochs. `IntervalSet` objects can interact with each other or with the time series objects.

    The `IntervalSet` object behaves like a numpy ndarray with the limitation that the object is not mutable.

    You can still apply any numpy array function to it :

    ``` py
    >>> import pynapple as nap
    >>> import numpy as np
    >>> ep = nap.IntervalSet(start=[0, 10], end=[5,20])
          start    end
     0        0      5
     1       10     20
    shape: (1, 2)        
    >>> np.diff(ep, 1)
    UserWarning: Converting IntervalSet to numpy.array
    array([[ 5.],
           [10.]])    
    ```

    You can slice :

    ``` py
    >>> ep[:,0]
    array([ 0., 10.])
    >>> ep[0]
          start    end
     0        0      5
    shape: (1, 2)
    ```

    But modifying the `IntervalSet` with raise an error:

    ``` py
    >>> ep[0,0] = 1
    RuntimeError: IntervalSet is immutable. Starts and ends have been already sorted.
    ```

"""

import importlib
import warnings
from numbers import Number

import numpy as np
import pandas as pd
from numpy.lib.mixins import NDArrayOperatorsMixin
from tabulate import tabulate

from ._jitted_functions import (
    _jitfix_iset,
    jitdiff,
    jitin_interval,
    jitintersect,
    jitunion,
)
from .config import nap_config
from .time_index import TsIndex
from .utils import (
    _get_terminal_size,
    _IntervalSetSliceHelper,
    check_filename,
    convert_to_numpy_array,
    is_array_like,
)

all_warnings = np.array(
    [
        "Some starts and ends are equal. Removing 1 microsecond!",
        "Some ends precede the relative start. Dropping them!",
        "Some starts precede the previous end. Joining them!",
        "Some epochs have no duration",
    ]
)


class IntervalSet(NDArrayOperatorsMixin):
    """
    A class representing a (irregular) set of time intervals in elapsed time, with relative operations
    """

    def __init__(self, start, end=None, time_units="s"):
        """
        IntervalSet initializer

        If start and end and not aligned, meaning that \n
        1. len(start) != len(end)
        2. end[i] > start[i]
        3. start[i+1] > end[i]
        4. start and end are not sorted,

        IntervalSet will try to "fix" the data by eliminating some of the start and end data point

        Parameters
        ----------
        start : numpy.ndarray or number or pandas.DataFrame or pandas.Series or iterable of (start, end) pairs
            Beginning of intervals. Alternatively, the `end` argument can be left out and `start` can be one of the
            following:
                - IntervalSet
                - pandas.DataFrame with columns ["start", "end"]
                - iterable of (start, end) pairs
                - a single (start, end) pair
        end : numpy.ndarray or number or pandas.Series, optional
            Ends of intervals
        time_units : str, optional
            Time unit of the intervals ('us', 'ms', 's' [default])

        Raises
        ------
        RuntimeError
            If `start` and `end` arguments are of unknown type

        """
        if isinstance(start, IntervalSet):
            end = start.end.astype(np.float64)
            start = start.start.astype(np.float64)

        elif isinstance(start, pd.DataFrame):
            assert (
                "start" in start.columns
                and "end" in start.columns
                and start.shape[-1] == 2
            ), """
                Wrong dataframe format. Expected format if passing a pandas dataframe is :
                    - 2 columns
                    - column names are ["start", "end"]                    
                """
            end = start["end"].values.astype(np.float64)
            start = start["start"].values.astype(np.float64)

        else:
            if end is None:
                # Require iterable of (start, end) tuples
                try:
                    start_end_array = np.array(list(start)).reshape(-1, 2)
                    start, end = zip(*start_end_array)
                except (TypeError, ValueError):
                    raise ValueError(
                        "Unable to Interpret the input. Please provide a list of start-end pairs."
                    )

            args = {"start": start, "end": end}

            for arg, data in args.items():
                if isinstance(data, Number):
                    args[arg] = np.array([data])
                elif isinstance(data, (list, tuple)):
                    args[arg] = np.ravel(np.array(data))
                elif isinstance(data, pd.Series):
                    args[arg] = data.values
                elif isinstance(data, np.ndarray):
                    args[arg] = np.ravel(data)
                elif is_array_like(data):
                    args[arg] = convert_to_numpy_array(data, arg)
                else:
                    raise RuntimeError(
                        "Unknown format for {}. Accepted formats are numpy.ndarray, list, tuple or any array-like objects.".format(
                            arg
                        )
                    )

            start = args["start"]
            end = args["end"]

            assert len(start) == len(end), "Starts end ends are not of the same length"

        start = TsIndex.format_timestamps(start, time_units)
        end = TsIndex.format_timestamps(end, time_units)

        if not (np.diff(start) > 0).all():
            warnings.warn("start is not sorted. Sorting it.", stacklevel=2)
            start = np.sort(start)

        if not (np.diff(end) > 0).all():
            warnings.warn("end is not sorted. Sorting it.", stacklevel=2)
            end = np.sort(end)

        data, to_warn = _jitfix_iset(start, end)

        if np.any(to_warn):
            msg = "\n".join(all_warnings[to_warn])
            warnings.warn(msg, stacklevel=2)

        self.values = data
        self.index = np.arange(data.shape[0], dtype="int")
        self.columns = np.array(["start", "end"])
        self.nap_class = self.__class__.__name__

    def __repr__(self):
        headers = [" " * 6, "start", "end"]
        bottom = "shape: {}, time unit: sec.".format(self.shape)

        rows = _get_terminal_size()[1]
        max_rows = np.maximum(rows - 10, 6)

        if len(self) > max_rows:
            n_rows = max_rows // 2
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return (
                    tabulate(
                        np.hstack(
                            (self.index[0:n_rows][:, None], self.values[0:n_rows])
                        ),
                        headers=headers,
                        tablefmt="plain",
                        colalign=("left", "center", "center"),
                    )
                    + "\n"
                    + " " * 10
                    + "..."
                    + tabulate(
                        np.hstack(
                            (self.index[-n_rows:][:, None], self.values[-n_rows:])
                        ),
                        headers=[
                            " " * 6,
                            " " * 5,
                            " " * 3,
                        ],  # To align properly the columns
                        tablefmt="plain",
                        colalign=("left", "center", "center"),
                    )
                    + "\n"
                    + bottom
                )

        else:
            return (
                tabulate(
                    self.values, headers=headers, showindex="always", tablefmt="plain"
                )
                + "\n"
                + bottom
            )

    def __str__(self):
        return self.__repr__()

    def __len__(self):
        return len(self.values)

    # def __iter__(self):
    #     pass

    def __setitem__(self, key, value):
        raise RuntimeError(
            "IntervalSet is immutable. Starts and ends have been already sorted."
        )

    def __getitem__(self, key, *args, **kwargs):
        if isinstance(key, str):
            if key == "start":
                return self.values[:, 0]
            elif key == "end":
                return self.values[:, 1]
            else:
                raise IndexError("Unknown string argument. Should be 'start' or 'end'")
        elif isinstance(key, Number):
            output = self.values.__getitem__(key)
            return IntervalSet(start=output[0], end=output[1])
        elif isinstance(key, (list, slice, np.ndarray)):
            output = self.values.__getitem__(key)
            return IntervalSet(start=output[:, 0], end=output[:, 1])
        elif isinstance(key, pd.Series):
            output = self.values.__getitem__(key)
            return IntervalSet(start=output[:, 0], end=output[:, 1])
        elif isinstance(key, tuple):
            if len(key) == 2:
                if isinstance(key[1], Number):
                    return self.values.__getitem__(key)
                elif key[1] == slice(None, None, None) or key[1] == slice(0, 2, None):
                    output = self.values.__getitem__(key)
                    return IntervalSet(start=output[:, 0], end=output[:, 1])
                else:
                    return self.values.__getitem__(key)
            else:
                raise IndexError(
                    "too many indices for IntervalSet: IntervalSet is 2-dimensional"
                )
        else:
            return self.values.__getitem__(key)

    def __array__(self, dtype=None):
        return self.values.astype(dtype)

    def __array_ufunc__(self, ufunc, method, *args, **kwargs):
        new_args = []
        for a in args:
            if isinstance(a, self.__class__):
                new_args.append(a.values)
            else:
                new_args.append(a)

        out = ufunc(*new_args, **kwargs)

        if not nap_config.suppress_conversion_warnings:
            warnings.warn(
                "Converting IntervalSet to numpy.array",
                UserWarning,
            )
        return out

    def __array_function__(self, func, types, args, kwargs):
        new_args = []
        for a in args:
            if isinstance(a, self.__class__):
                new_args.append(a.values)
            else:
                new_args.append(a)

        out = func._implementation(*new_args, **kwargs)

        if not nap_config.suppress_conversion_warnings:
            warnings.warn(
                "Converting IntervalSet to numpy.array",
                UserWarning,
            )
        return out

    @property
    def start(self):
        return self.values[:, 0]

    @property
    def end(self):
        return self.values[:, 1]

    @property
    def shape(self):
        return self.values.shape

    @property
    def ndim(self):
        return self.values.ndim

    @property
    def size(self):
        return self.values.size

    @property
    def starts(self):
        """Return the starts of the IntervalSet as a Ts object

        Returns
        -------
        Ts
            The starts of the IntervalSet
        """
        warnings.warn(
            "starts is a deprecated function. It will be removed in future versions",
            category=DeprecationWarning,
            stacklevel=2,
        )
        time_series = importlib.import_module(".time_series", "pynapple.core")
        return time_series.Ts(t=self.values[:, 0])

    @property
    def ends(self):
        """Return the ends of the IntervalSet as a Ts object

        Returns
        -------
        Ts
            The ends of the IntervalSet
        """
        warnings.warn(
            "ends is a deprecated function. It will be removed in future versions",
            category=DeprecationWarning,
            stacklevel=2,
        )
        time_series = importlib.import_module(".time_series", "pynapple.core")
        return time_series.Ts(t=self.values[:, 1])

    @property
    def loc(self):
        """
        Slicing function to add compatibility with pandas DataFrame after removing it as a super class of IntervalSet
        """
        return _IntervalSetSliceHelper(self)

    @classmethod
    def _from_npz_reader(cls, file):
        """Load an IntervalSet object from a npz file.

        The file should contain the keys 'start', 'end' and 'type'.
        The 'type' key should be 'IntervalSet'.

        Parameters
        ----------
        file : NPZFile object
            opened npz file interface.

        Returns
        -------
        IntervalSet
            The IntervalSet object
        """
        return cls(start=file["start"], end=file["end"])

    def time_span(self):
        """
        Time span of the interval set.

        Returns
        -------
        out: IntervalSet
            an IntervalSet with a single interval encompassing the whole IntervalSet
        """
        s = self.values[0, 0]
        e = self.values[-1, 1]
        return IntervalSet(s, e)

    def tot_length(self, time_units="s"):
        """
        Total elapsed time in the set.

        Parameters
        ----------
        time_units : None, optional
            The time units to return the result in ('us', 'ms', 's' [default])

        Returns
        -------
        out: float
            _
        """
        tot_l = np.sum(self.values[:, 1] - self.values[:, 0])
        return TsIndex.return_timestamps(np.array([tot_l]), time_units)[0]

    def intersect(self, a):
        """
        Set intersection of IntervalSet

        Parameters
        ----------
        a : IntervalSet
            the IntervalSet to intersect self with

        Returns
        -------
        out: IntervalSet
            _
        """
        start1 = self.values[:, 0]
        end1 = self.values[:, 1]
        start2 = a.values[:, 0]
        end2 = a.values[:, 1]
        s, e = jitintersect(start1, end1, start2, end2)
        return IntervalSet(s, e)

    def union(self, a):
        """
        set union of IntervalSet

        Parameters
        ----------
        a : IntervalSet
            the IntervalSet to union self with

        Returns
        -------
        out: IntervalSet
            _
        """
        start1 = self.values[:, 0]
        end1 = self.values[:, 1]
        start2 = a.values[:, 0]
        end2 = a.values[:, 1]
        s, e = jitunion(start1, end1, start2, end2)
        return IntervalSet(s, e)

    def set_diff(self, a):
        """
        set difference of IntervalSet

        Parameters
        ----------
        a : IntervalSet
            the IntervalSet to set-substract from self

        Returns
        -------
        out: IntervalSet
            _
        """
        start1 = self.values[:, 0]
        end1 = self.values[:, 1]
        start2 = a.values[:, 0]
        end2 = a.values[:, 1]
        s, e = jitdiff(start1, end1, start2, end2)
        return IntervalSet(s, e)

    def in_interval(self, tsd):
        """
        finds out in which element of the interval set each point in a time series fits.

        NaNs for those that don't fit an interval

        Parameters
        ----------
        tsd : Tsd
            The tsd to be binned

        Returns
        -------
        out: numpy.ndarray
            an array with the interval index labels for each time stamp (NaN) for timestamps not in IntervalSet
        """
        times = tsd.index.values
        starts = self.values[:, 0]
        ends = self.values[:, 1]

        return jitin_interval(times, starts, ends)

    def drop_short_intervals(self, threshold, time_units="s"):
        """
        Drops the short intervals in the interval set with duration shorter than `threshold`.

        Parameters
        ----------
        threshold : numeric
            Time threshold for "short" intervals
        time_units : None, optional
            The time units for the treshold ('us', 'ms', 's' [default])

        Returns
        -------
        out: IntervalSet
            A copied IntervalSet with the dropped intervals
        """
        threshold = TsIndex.format_timestamps(
            np.array([threshold], dtype=np.float64), time_units
        )[0]
        return self[(self.values[:, 1] - self.values[:, 0]) > threshold]

    def drop_long_intervals(self, threshold, time_units="s"):
        """
        Drops the long intervals in the interval set with duration longer than `threshold`.

        Parameters
        ----------
        threshold : numeric
            Time threshold for "long" intervals
        time_units : None, optional
            The time units for the treshold ('us', 'ms', 's' [default])

        Returns
        -------
        out: IntervalSet
            A copied IntervalSet with the dropped intervals
        """
        threshold = TsIndex.format_timestamps(
            np.array([threshold], dtype=np.float64), time_units
        )[0]
        return self[(self.values[:, 1] - self.values[:, 0]) < threshold]

    def as_units(self, units="s"):
        """
        returns a pandas DataFrame with time expressed in the desired unit

        Parameters
        ----------
        units : None, optional
            'us', 'ms', or 's' [default]

        Returns
        -------
        out: pandas.DataFrame
            DataFrame with adjusted times
        """

        data = self.values.copy()
        data = TsIndex.return_timestamps(data, units)
        if units == "us":
            data = data.astype(np.int64)

        df = pd.DataFrame(index=self.index, data=data, columns=self.columns)

        return df

    def merge_close_intervals(self, threshold, time_units="s"):
        """
        Merges intervals that are very close.

        Parameters
        ----------
        threshold : numeric
            time threshold for the closeness of the intervals
        time_units : None, optional
            time units for the threshold ('us', 'ms', 's' [default])

        Returns
        -------
        out: IntervalSet
            a copied IntervalSet with merged intervals

        """
        if len(self) == 0:
            return IntervalSet(start=[], end=[])

        threshold = TsIndex.format_timestamps(
            np.array((threshold,), dtype=np.float64).ravel(), time_units
        )[0]
        start = self.values[:, 0]
        end = self.values[:, 1]
        tojoin = (start[1:] - end[0:-1]) > threshold
        start = np.hstack((start[0], start[1:][tojoin]))
        end = np.hstack((end[0:-1][tojoin], end[-1]))

        return IntervalSet(start=start, end=end)

    def get_intervals_center(self, alpha=0.5):
        """
        Returns by default the centers of each intervals.

        It is possible to bias the midpoint by changing the alpha parameter between [0, 1]
        For each epoch:
        t = start + (end-start)*alpha

        Parameters
        ----------
        alpha : float, optional
            The midpoint within each interval.

        Returns
        -------
        Ts
            Timestamps object
        """
        time_series = importlib.import_module(".time_series", "pynapple.core")
        starts = self.values[:, 0]
        ends = self.values[:, 1]

        if not isinstance(alpha, float):
            raise RuntimeError("Parameter alpha should be float type")

        alpha = np.clip(alpha, 0, 1)
        t = starts + (ends - starts) * alpha
        return time_series.Ts(t=t, time_support=self)

    def as_dataframe(self):
        """
        Convert the `IntervalSet` object to a pandas.DataFrame object.

        Returns
        -------
        out: pandas.DataFrame
            _
        """
        return pd.DataFrame(data=self.values, columns=["start", "end"])

    def save(self, filename):
        """
        Save IntervalSet object in npz format. The file will contain the starts and ends.

        The main purpose of this function is to save small/medium sized IntervalSet
        objects. For example, you determined some epochs for one session that you want to save
        to avoid recomputing them.

        You can load the object with `nap.load_file`. Keys are 'start', 'end' and 'type'.
        See the example below.

        Parameters
        ----------
        filename : str
            The filename

        Examples
        --------
        >>> import pynapple as nap
        >>> import numpy as np
        >>> ep = nap.IntervalSet(start=[0, 10, 20], end=[5, 12, 33])
        >>> ep.save("my_ep.npz")

        To load you file, you can use the `nap.load_file` function :

        >>> ep = nap.load_file("my_path/my_ep.npz")
        >>> ep
           start   end
        0    0.0   5.0
        1   10.0  12.0
        2   20.0  33.0

        Raises
        ------
        RuntimeError
            If filename is not str, path does not exist or filename is a directory.
        """
        np.savez(
            check_filename(filename),
            start=self.values[:, 0],
            end=self.values[:, 1],
            type=np.array(["IntervalSet"], dtype=np.str_),
        )

        return

    def split(self, interval_size, time_units="s"):
        """Split `IntervalSet` to a new `IntervalSet` with each interval being of size `interval_size`.

        Used mostly for chunking very large dataset or looping throught multiple epoch of same duration.

        This function skips the epochs that are shorter than `interval_size`.

        Note that intervals are strictly non-overlapping in pynapple. One microsecond is removed from contiguous intervals.

        Parameters
        ----------
        interval_size : Number
            Description
        time_units : str, optional
            time units for the `interval_size` ('us', 'ms', 's' [default])

        Returns
        -------
        IntervalSet
            New `IntervalSet` with equal sized intervals

        Raises
        ------
        IOError
            If `interval_size` is not a Number or is below 0
            If `time_units` is not a string
        """
        if not isinstance(interval_size, Number):
            raise IOError("Argument interval_size should of type float or int")

        if not interval_size > 0:
            raise IOError("Argument interval_size should be strictly larger than 0")

        if not isinstance(time_units, str):
            raise IOError("Argument time_units should be of type str")

        if len(self) == 0:
            return IntervalSet(start=[], end=[])

        interval_size = TsIndex.format_timestamps(
            np.array((interval_size,), dtype=np.float64).ravel(), time_units
        )[0]

        interval_size = np.round(interval_size, nap_config.time_index_precision)

        durations = np.round(self.end - self.start, nap_config.time_index_precision)

        idxs = np.where(durations > interval_size)[0]
        size_tmp = (
            np.ceil((self.end[idxs] - self.start[idxs]) / interval_size)
        ).astype(int) + 1
        new_starts = np.full(size_tmp.sum() - size_tmp.shape[0], np.nan)
        new_ends = np.full(size_tmp.sum() - size_tmp.shape[0], np.nan)
        i0 = 0
        for cnt, idx in enumerate(idxs):
            new_starts[i0 : i0 + size_tmp[cnt] - 1] = np.arange(
                self.start[idx], self.end[idx], interval_size
            )
            new_ends[i0 : i0 + size_tmp[cnt] - 2] = new_starts[
                i0 + 1 : i0 + size_tmp[cnt] - 1
            ]
            new_ends[i0 + size_tmp[cnt] - 2] = self.end[idx]
            i0 += size_tmp[cnt] - 1
        new_starts = np.round(new_starts, nap_config.time_index_precision)
        new_ends = np.round(new_ends, nap_config.time_index_precision)

        durations = np.round(new_ends - new_starts, nap_config.time_index_precision)
        tokeep = durations >= interval_size
        new_starts = new_starts[tokeep]
        new_ends = new_ends[tokeep]

        # Removing 1 microsecond to have strictly non-overlapping intervals for intervals coming from the same epoch
        new_ends -= 1e-6

        return IntervalSet(new_starts, new_ends)
