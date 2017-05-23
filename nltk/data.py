# Natural Language Toolkit: Utility functions
#
# Copyright (C) 2001-2017 NLTK Project
# Author: Edward Loper <edloper@gmail.com>
# URL: <http://nltk.org/>
# For license information, see LICENSE.TXT

"""
Functions to find and load NLTK resource files, such as corpora,
grammars, and saved processing objects.  Resource files are identified
using URLs, such as ``nltk:corpora/abc/rural.txt`` or
``http://nltk.org/sample/toy.cfg``.  The following URL protocols are
supported:

  - ``file:path``: Specifies the file whose path is *path*.
    Both relative and absolute paths may be used.

  - ``http://host/path``: Specifies the file stored on the web
    server *host* at path *path*.

  - ``nltk:path``: Specifies the file stored in the NLTK data
    package at *path*.  NLTK will search for these files in the
    directories specified by ``nltk.data.path``.

If no protocol is specified, then the default protocol ``nltk:`` will
be used.

This module provides to functions that can be used to access a
resource file, given its URL: ``load()`` loads a given resource, and
adds it to a resource cache; and ``retrieve()`` copies a given resource
to a local file.
"""
from __future__ import print_function, unicode_literals
from __future__ import division
from abc import ABCMeta, abstractmethod
from six import add_metaclass

import sys
import io
import os
import textwrap
import re
import zipfile

from gzip import GzipFile, READ as GZ_READ, WRITE as GZ_WRITE

try:
    from zlib import Z_SYNC_FLUSH as FLUSH
except ImportError:
    from zlib import Z_FINISH as FLUSH

try:
    import cPickle as pickle
except ImportError:
    import pickle

from six import string_types, text_type
from six.moves.urllib.request import urlopen, url2pathname

# this import should be more specific:
import nltk
from nltk.compat import py3_data, add_py3_data, BytesIO

######################################################################
# Search Path
######################################################################

path = []
"""A list of directories where the NLTK data package might reside.
   These directories will be checked in order when looking for a
   resource in the data package.  Note that this allows users to
   substitute in their own versions of resources, if they have them
   (e.g., in their home directory under ~/nltk_data)."""

# User-specified locations:
_paths_from_env = os.environ.get('NLTK_DATA', str('')).split(os.pathsep)
path += [d for d in _paths_from_env if d]
if 'APPENGINE_RUNTIME' not in os.environ and os.path.expanduser('~/') != '~/':
    path.append(os.path.expanduser(str('~/nltk_data')))

if sys.platform.startswith('win'):
    # Common locations on Windows:
    path += [
        str(r'C:\nltk_data'), str(r'D:\nltk_data'), str(r'E:\nltk_data'),
        os.path.join(sys.prefix, str('nltk_data')),
        os.path.join(sys.prefix, str('lib'), str('nltk_data')),
        os.path.join(
            os.environ.get(str('APPDATA'), str('C:\\')), str('nltk_data'))
    ]
else:
    # Common locations on UNIX & OS X:
    path += [
        str('/usr/share/nltk_data'),
        str('/usr/local/share/nltk_data'),
        str('/usr/lib/nltk_data'),
        str('/usr/local/lib/nltk_data')
    ]


######################################################################
# Util Functions
######################################################################

def gzip_open_unicode(filename, mode="rb", compresslevel=9, encoding='utf-8',
                      fileobj=None, errors=None, newline=None):
    if fileobj is None:
        fileobj = GzipFile(filename, mode, compresslevel, fileobj)
    return io.TextIOWrapper(fileobj, encoding, errors, newline)


def split_resource_url(resource_url):
    """
    Splits a resource url into "<protocol>:<path>".

    >>> windows = sys.platform.startswith('win')
    >>> split_resource_url('nltk:home/nltk')
    ('nltk', 'home/nltk')
    >>> split_resource_url('nltk:/home/nltk')
    ('nltk', '/home/nltk')
    >>> split_resource_url('file:/home/nltk')
    ('file', '/home/nltk')
    >>> split_resource_url('file:///home/nltk')
    ('file', '/home/nltk')
    >>> split_resource_url('file:///C:/home/nltk')
    ('file', '/C:/home/nltk')
    """
    protocol, path_ = resource_url.split(':', 1)
    if protocol == 'nltk':
        pass
    elif protocol == 'file':
        if path_.startswith('/'):
            path_ = '/' + path_.lstrip('/')
    else:
        path_ = re.sub(r'^/{0,2}', '', path_)
    return protocol, path_


def normalize_resource_url(resource_url):
    r"""
    Normalizes a resource url

    >>> windows = sys.platform.startswith('win')
    >>> os.path.normpath(split_resource_url(normalize_resource_url('file:grammar.fcfg'))[1]) == \
    ... ('\\' if windows else '') + os.path.abspath(os.path.join(os.curdir, 'grammar.fcfg'))
    True
    >>> not windows or normalize_resource_url('file:C:/dir/file') == 'file:///C:/dir/file'
    True
    >>> not windows or normalize_resource_url('file:C:\\dir\\file') == 'file:///C:/dir/file'
    True
    >>> not windows or normalize_resource_url('file:C:\\dir/file') == 'file:///C:/dir/file'
    True
    >>> not windows or normalize_resource_url('file://C:/dir/file') == 'file:///C:/dir/file'
    True
    >>> not windows or normalize_resource_url('file:////C:/dir/file') == 'file:///C:/dir/file'
    True
    >>> not windows or normalize_resource_url('nltk:C:/dir/file') == 'file:///C:/dir/file'
    True
    >>> not windows or normalize_resource_url('nltk:C:\\dir\\file') == 'file:///C:/dir/file'
    True
    >>> windows or normalize_resource_url('file:/dir/file/toy.cfg') == 'file:///dir/file/toy.cfg'
    True
    >>> normalize_resource_url('nltk:home/nltk')
    'nltk:home/nltk'
    >>> windows or normalize_resource_url('nltk:/home/nltk') == 'file:///home/nltk'
    True
    >>> normalize_resource_url('http://example.com/dir/file')
    'http://example.com/dir/file'
    >>> normalize_resource_url('dir/file')
    'nltk:dir/file'
    """
    try:
        protocol, name = split_resource_url(resource_url)
    except ValueError:
        # the resource url has no protocol, use the nltk protocol by default
        protocol = 'nltk'
        name = resource_url
    # use file protocol if the path is an absolute path
    if protocol == 'nltk' and os.path.isabs(name):
        protocol = 'file://'
        name = normalize_resource_name(name, False, None)
    elif protocol == 'file':
        protocol = 'file://'
        # name is absolute
        name = normalize_resource_name(name, False, None)
    elif protocol == 'nltk':
        protocol = 'nltk:'
        name = normalize_resource_name(name, True)
    else:
        # handled by urllib
        protocol += '://'
    return ''.join([protocol, name])


def normalize_resource_name(resource_name, allow_relative=True, relative_path=None):
    """
    :type resource_name: str or unicode
    :param resource_name: The name of the resource to search for.
        Resource names are posix-style relative path names, such as
        ``corpora/brown``.  Directory names will automatically
        be converted to a platform-appropriate path separator.
        Directory trailing slashes are preserved

    >>> windows = sys.platform.startswith('win')
    >>> normalize_resource_name('.', True)
    './'
    >>> normalize_resource_name('./', True)
    './'
    >>> windows or normalize_resource_name('dir/file', False, '/') == '/dir/file'
    True
    >>> not windows or normalize_resource_name('C:/file', False, '/') == '/C:/file'
    True
    >>> windows or normalize_resource_name('/dir/file', False, '/') == '/dir/file'
    True
    >>> windows or normalize_resource_name('../dir/file', False, '/') == '/dir/file'
    True
    >>> not windows or normalize_resource_name('/dir/file', True, '/') == 'dir/file'
    True
    >>> windows or normalize_resource_name('/dir/file', True, '/') == '/dir/file'
    True
    """
    is_dir = bool(re.search(r'[\\/.]$', resource_name)) or resource_name.endswith(os.path.sep)
    if sys.platform.startswith('win'):
        resource_name = resource_name.lstrip('/')
    else:
        resource_name = re.sub(r'^/+', '/', resource_name)
    if allow_relative:
        resource_name = os.path.normpath(resource_name)
    else:
        if relative_path is None:
            relative_path = os.curdir
        resource_name = os.path.abspath(
            os.path.join(relative_path, resource_name))
    resource_name = resource_name.replace('\\', '/').replace(os.path.sep, '/')
    if sys.platform.startswith('win') and os.path.isabs(resource_name):
        resource_name = '/' + resource_name
    if is_dir and not resource_name.endswith('/'):
        resource_name += '/'
    return resource_name


######################################################################
# Path Pointers
######################################################################

@add_metaclass(ABCMeta)
class PathPointer(object):
    """
    An abstract base class for 'path pointers,' used by NLTK's data
    package to identify specific paths.  Two subclasses exist:
    ``FileSystemPathPointer`` identifies a file that can be accessed
    directly via a given absolute path.  ``ZipFilePathPointer``
    identifies a file contained within a zipfile, that can be accessed
    by reading that zipfile.
    """

    @abstractmethod
    def open(self, encoding=None):
        """
        Return a seekable read-only stream that can be used to read
        the contents of the file identified by this path pointer.

        :raise IOError: If the path specified by this pointer does
            not contain a readable file.
        """

    @abstractmethod
    def file_size(self):
        # fixme
        """
        Return the size of the file pointed to by this path pointer,
        in bytes.

        :raise IOError: If the path specified by this pointer does
            not contain a readable file.
        """

    @abstractmethod
    def join(self, fileid):
        """
        Return a new path pointer formed by starting at the path
        identified by this pointer, and then following the relative
        path given by ``fileid``.  The path components of ``fileid``
        should be separated by forward slashes, regardless of
        the underlying file system's path seperator character.
        """


class FileSystemPathPointer(PathPointer, text_type):
    """
    A path pointer that identifies a file which can be accessed
    directly via a given absolute path.
    """
    @py3_data
    def __init__(self, _path):
        """
        Create a new path pointer for the given absolute path.

        :raise IOError: If the given path does not exist.
        """

        _path = os.path.abspath(_path)
        if not os.path.exists(_path):
            raise IOError('No such file or directory: %r' % _path)
        self._path = _path

        # There's no need to call str.__init__(), since it's a no-op;
        # str does all of its setup work in __new__.

    @property
    def path(self):
        """The absolute path identified by this path pointer."""
        return self._path

    def open(self, encoding=None):
        if encoding is None:
            stream = io.open(self._path, 'rb')
        else:
            stream = io.open(self._path, 'rt', encoding=encoding)
        return stream

    def file_size(self):
        # fixme: gives wrong answer for encoded files
        raise NotImplementedError
        return os.stat(self._path).st_size

    def join(self, fileid):
        _path = os.path.join(self._path, fileid)
        return FileSystemPathPointer(_path)

    def __repr__(self):
        # This should be a byte string under Python 2.x;
        # we don't want transliteration here so
        # @python_2_unicode_compatible is not used.
        return str('FileSystemPathPointer(%r)' % self._path)

    def __str__(self):
        return self._path


class BufferedGzipFile(GzipFile):
    """
    A ``GzipFile`` subclass that buffers calls to ``read()`` and ``write()``.
    This allows faster reads and writes of data to and from gzip-compressed
    files at the cost of using more memory.

    The default buffer size is 2MB.

    ``BufferedGzipFile`` is useful for loading large gzipped pickle objects
    as well as writing large encoded feature files for classifier training.
    """
    MB = 2 ** 20
    SIZE = 2 * MB

    @py3_data
    def __init__(self, filename=None, mode=None, compresslevel=9,
                 fileobj=None, **kwargs):
        """
        Return a buffered gzip file object.

        :param filename: a filesystem path
        :type filename: str
        :param mode: a file mode which can be any of 'r', 'rb', 'a', 'ab',
            'w', or 'wb'
        :type mode: str
        :param compresslevel: The compresslevel argument is an integer from 1
            to 9 controlling the level of compression; 1 is fastest and
            produces the least compression, and 9 is slowest and produces the
            most compression. The default is 9.
        :type compresslevel: int
        :param fileobj: a BytesIO stream to read from instead of a file.
        :type fileobj: BytesIO
        :param size: number of bytes to buffer during calls to read() and write()
        :type size: int
        :rtype: BufferedGzipFile
        """
        GzipFile.__init__(self, filename, mode, compresslevel, fileobj)
        self._size = kwargs.get('size', self.SIZE)
        self._nltk_buffer = BytesIO()
        # cStringIO does not support len.
        self._len = 0

    def _reset_buffer(self):
        # For some reason calling BytesIO.truncate() here will lead to
        # inconsistent writes so just set _buffer to a new BytesIO object.
        self._nltk_buffer = BytesIO()
        self._len = 0

    def _write_buffer(self, data):
        # Simply write to the buffer and increment the buffer size.
        if data is not None:
            self._nltk_buffer.write(data)
            self._len += len(data)

    def _write_gzip(self, data):
        # Write the current buffer to the GzipFile.
        GzipFile.write(self, self._nltk_buffer.getvalue())
        # Then reset the buffer and write the new data to the buffer.
        self._reset_buffer()
        self._write_buffer(data)

    def close(self):
        # GzipFile.close() doesn't actuallly close anything.
        if self.mode == GZ_WRITE:
            self._write_gzip(None)
            self._reset_buffer()
        return GzipFile.close(self)

    def flush(self, lib_mode=FLUSH):
        self._nltk_buffer.flush()
        GzipFile.flush(self, lib_mode)

    def read(self, size=None):
        if not size:
            size = self._size
            contents = BytesIO()
            while True:
                blocks = GzipFile.read(self, size)
                if not blocks:
                    contents.flush()
                    break
                contents.write(blocks)
            return contents.getvalue()
        else:
            return GzipFile.read(self, size)

    def write(self, data, size=-1):
        """
        :param data: bytes to write to file or buffer
        :type data: bytes
        :param size: buffer at least size bytes before writing to file
        :type size: int
        """
        if not size:
            size = self._size
        if self._len + len(data) <= size:
            self._write_buffer(data)
        else:
            self._write_gzip(data)


class GzipFileSystemPathPointer(FileSystemPathPointer):
    """
    A subclass of ``FileSystemPathPointer`` that identifies a gzip-compressed
    file located at a given absolute path.  ``GzipFileSystemPathPointer`` is
    appropriate for loading large gzip-compressed pickle objects efficiently.
    """

    def open(self, encoding=None):
        # Note: In >= Python3.5, GzipFile is already using a
        # buffered reader in the backend which has a variable self._buffer
        # See https://github.com/nltk/nltk/issues/1308
        if sys.version.startswith('2.7') or sys.version.startswith('3.4'):
            stream = BufferedGzipFile(self._path, 'rb')
        else:
            stream = GzipFile(self._path, 'rb')
        if encoding:
            stream = io.TextIOWrapper(stream, encoding)
        return stream


class ZipFilePathPointer(PathPointer):
    """
    A path pointer that identifies a file contained within a zipfile,
    which can be accessed by reading that zipfile.
    """
    @py3_data
    def __init__(self, zipfile, entry=''):
        """
        Create a new path pointer pointing at the specified entry
        in the given zipfile.

        :raise IOError: If the given zipfile does not exist, or if it
        does not contain the specified entry.
        """
        if isinstance(zipfile, string_types):
            zipfile = OpenOnDemandZipFile(os.path.abspath(zipfile))

        # Normalize the entry string, it should be relative:
        entry = normalize_resource_name(entry, True, '/').lstrip('/')

        # Check that the entry exists:
        if entry:
            try:
                zipfile.getinfo(entry)
            except Exception:
                # Sometimes directories aren't explicitly listed in
                # the zip file.  So if `entry` is a directory name,
                # then check if the zipfile contains any files that
                # are under the given directory.
                if (entry.endswith('/') and
                        [n for n in zipfile.namelist() if n.startswith(entry)]):
                    pass  # zipfile contains a file in that directory.
                else:
                    # Otherwise, complain.
                    raise IOError('Zipfile %r does not contain %r' %
                                  (zipfile.filename, entry))
        self._zipfile = zipfile
        self._entry = entry

    @property
    def zipfile(self):
        """
        The zipfile.ZipFile object used to access the zip file
        containing the entry identified by this path pointer.
        """
        return self._zipfile

    @property
    def entry(self):
        """
        The name of the file within zipfile that this path
        pointer points to.
        """
        return self._entry

    def open(self, encoding=None):
        data = self._zipfile.read(self._entry)
        if self._entry.endswith('.gz'):
            # Note: In >= Python3.5, GzipFile is already using a
            # buffered reader in the backend which has a variable self._buffer
            # See https://github.com/nltk/nltk/issues/1308
            if sys.version.startswith('2.7') or sys.version.startswith('3.4'):
                stream = BufferedGzipFile(self._entry, fileobj=stream)
            else:
                stream = GzipFile(self._entry, fileobj=stream)
            if encoding:
                stream = io.TextIOWrapper(stream, encoding)
        elif encoding is None:
            stream = io.BytesIO(data)
        else:
            stream = io.StringIO(data.decode(encoding))
        return stream

    def file_size(self):
        # fixme: gives length in bytes, not chars
        raise NotImplementedError
        return self._zipfile.getinfo(self._entry).file_size

    def join(self, fileid):
        entry = '%s/%s' % (self._entry, fileid)
        return ZipFilePathPointer(self._zipfile, entry)

    def __repr__(self):
        return str('ZipFilePathPointer(%r, %r)') % (
            self._zipfile.filename, self._entry)

    def __str__(self):
        return os.path.normpath(os.path.join(self._zipfile.filename,
                                             self._entry))


######################################################################
# Access Functions
######################################################################

# Don't use a weak dictionary, because in the common case this
# causes a lot more reloading that necessary.
_resource_cache = {}
"""A dictionary used to cache resources so that they won't
   need to be loaded more than once."""


def find(resource_name, paths=None):
    """
    Find the given resource by searching through the directories and
    zip files in paths, where a None or empty string specifies an absolute path.
    Returns a corresponding path name.  If the given resource is not
    found, raise a ``LookupError``, whose message gives a pointer to
    the installation instructions for the NLTK downloader.

    Zip File Handling:

      - If ``resource_name`` contains a component with a ``.zip``
        extension, then it is assumed to be a zipfile; and the
        remaining path components are used to look inside the zipfile.

      - If any element of ``nltk.data.path`` has a ``.zip`` extension,
        then it is assumed to be a zipfile.

      - If a given resource name that does not contain any zipfile
        component is not found initially, then ``find()`` will make a
        second attempt to find that resource, by replacing each
        component *p* in the path with *p.zip/p*.  For example, this
        allows ``find()`` to map the resource name
        ``corpora/chat80/cities.pl`` to a zip file path pointer to
        ``corpora/chat80.zip/chat80/cities.pl``.

      - When using ``find()`` to locate a directory contained in a
        zipfile, the resource name must end with the forward slash
        character.  Otherwise, ``find()`` will not locate the
        directory.

    :type resource_name: str or unicode
    :param resource_name: The name of the resource to search for.
        Resource names are posix-style relative path names, such as
        ``corpora/brown``.  Directory names will be
        automatically converted to a platform-appropriate path separator.
    :rtype: str
    """
    resource_name = normalize_resource_name(resource_name, True)

    # Resolve default paths at runtime in-case the user overrides
    # nltk.data.path
    if paths is None:
        paths = path

    # Check if the resource name includes a zipfile name
    m = re.match(r'(.*\.zip)/?(.*)$|', resource_name)
    zipfile, zipentry = m.groups()

    # Check each item in our path
    for path_ in paths:
        # Is the path item a zipfile?
        if path_ and (os.path.isfile(path_) and path_.endswith('.zip')):
            try:
                return ZipFilePathPointer(path_, resource_name)
            except IOError:
                # resource not in zipfile
                continue

        # Is the path item a directory or is resource_name an absolute path?
        elif not path_ or os.path.isdir(path_):
            if zipfile is None:
                p = os.path.join(path_, url2pathname(resource_name))
                if os.path.exists(p):
                    if p.endswith('.gz'):
                        return GzipFileSystemPathPointer(p)
                    else:
                        return FileSystemPathPointer(p)
            else:
                p = os.path.join(path_, url2pathname(zipfile))
                if os.path.exists(p):
                    try:
                        return ZipFilePathPointer(p, zipentry)
                    except IOError:
                        # resource not in zipfile
                        continue

    # Fallback: if the path doesn't include a zip file, then try
    # again, assuming that one of the path components is inside a
    # zipfile of the same name.
    if zipfile is None:
        pieces = resource_name.split('/')
        for i in range(len(pieces)):
            modified_name = '/'.join(pieces[:i] +
                                     [pieces[i] + '.zip'] + pieces[i:])
            try:
                return find(modified_name, paths)
            except LookupError:
                pass

    # Display a friendly error message if the resource wasn't found:
    msg = textwrap.fill(
        'Resource %r not found.  Please use the NLTK Downloader to '
        'obtain the resource:  >>> nltk.download()' %
        (resource_name,), initial_indent='  ', subsequent_indent='  ',
        width=66)
    msg += '\n  Searched in:' + ''.join('\n    - %r' % d for d in paths)
    sep = '*' * 70
    resource_not_found = '\n%s\n%s\n%s' % (sep, msg, sep)
    raise LookupError(resource_not_found)


def retrieve(resource_url, filename=None, verbose=True):
    """
    Copy the given resource to a local file.  If no filename is
    specified, then use the URL's filename.  If there is already a
    file named ``filename``, then raise a ``ValueError``.

    :type resource_url: str
    :param resource_url: A URL specifying where the resource should be
        loaded from.  The default protocol is "nltk:", which searches
        for the file in the the NLTK data package.
    """
    resource_url = normalize_resource_url(resource_url)
    if filename is None:
        if resource_url.startswith('file:'):
            filename = os.path.split(resource_url)[-1]
        else:
            filename = re.sub(r'(^\w+:)?.*/', '', resource_url)
    if os.path.exists(filename):
        filename = os.path.abspath(filename)
        raise ValueError("File %r already exists!" % filename)

    if verbose:
        print('Retrieving %r, saving to %r' % (resource_url, filename))

    # Open the input & output streams.
    infile = _open(resource_url)

    # Copy infile -> outfile, using 64k blocks.
    with open(filename, "wb") as outfile:
        while True:
            s = infile.read(1024 * 64)  # 64k blocks.
            outfile.write(s)
            if not s:
                break

    infile.close()


#: A dictionary describing the formats that are supported by NLTK's
#: load() method.  Keys are format names, and values are format
#: descriptions.
FORMATS = {
    'pickle': "A serialized python object, stored using the pickle module.",
    'json': "A serialized python object, stored using the json module.",
    'yaml': "A serialized python object, stored using the yaml module.",
    'cfg': "A context free grammar.",
    'pcfg': "A probabilistic CFG.",
    'fcfg': "A feature CFG.",
    'fol': "A list of first order logic expressions, parsed with "
            "nltk.sem.logic.Expression.fromstring.",
    'logic': "A list of first order logic expressions, parsed with "
            "nltk.sem.logic.LogicParser.  Requires an additional logic_parser "
            "parameter",
    'val': "A semantic valuation, parsed by nltk.sem.Valuation.fromstring.",
    'raw': "The raw (byte string) contents of a file.",
    'text': "The raw (unicode string) contents of a file. "
}

#: A dictionary mapping from file extensions to format names, used
#: by load() when format="auto" to decide the format for a
#: given resource url.
AUTO_FORMATS = {
    'pickle': 'pickle',
    'json': 'json',
    'yaml': 'yaml',
    'cfg': 'cfg',
    'pcfg': 'pcfg',
    'fcfg': 'fcfg',
    'fol': 'fol',
    'logic': 'logic',
    'val': 'val',
    'txt': 'text',
    'text': 'text',
}


def load(resource_url, format='auto', cache=True, verbose=False,
         logic_parser=None, fstruct_reader=None, encoding=None):
    """
    Load a given resource from the NLTK data package.  The following
    resource formats are currently supported:

      - ``pickle``
      - ``json``
      - ``yaml``
      - ``cfg`` (context free grammars)
      - ``pcfg`` (probabilistic CFGs)
      - ``fcfg`` (feature-based CFGs)
      - ``fol`` (formulas of First Order Logic)
      - ``logic`` (Logical formulas to be parsed by the given logic_parser)
      - ``val`` (valuation of First Order Logic model)
      - ``text`` (the file contents as a unicode string)
      - ``raw`` (the raw file contents as a byte string)

    If no format is specified, ``load()`` will attempt to determine a
    format based on the resource name's file extension.  If that
    fails, ``load()`` will raise a ``ValueError`` exception.

    For all text formats (everything except ``pickle``, ``json``, ``yaml`` and ``raw``),
    it tries to decode the raw contents using UTF-8, and if that doesn't
    work, it tries with ISO-8859-1 (Latin-1), unless the ``encoding``
    is specified.

    :type resource_url: str
    :param resource_url: A URL specifying where the resource should be
        loaded from.  The default protocol is "nltk:", which searches
        for the file in the the NLTK data package.
    :type cache: bool
    :param cache: If true, add this resource to a cache.  If load()
        finds a resource in its cache, then it will return it from the
        cache rather than loading it.  The cache uses weak references,
        so a resource wil automatically be expunged from the cache
        when no more objects are using it.
    :type verbose: bool
    :param verbose: If true, print a message when loading a resource.
        Messages are not displayed when a resource is retrieved from
        the cache.
    :type logic_parser: LogicParser
    :param logic_parser: The parser that will be used to parse logical
        expressions.
    :type fstruct_reader: FeatStructReader
    :param fstruct_reader: The parser that will be used to parse the
        feature structure of an fcfg.
    :type encoding: str
    :param encoding: the encoding of the input; only used for text formats.
    """
    resource_url = normalize_resource_url(resource_url)
    resource_url = add_py3_data(resource_url)

    # Determine the format of the resource.
    if format == 'auto':
        resource_url_parts = resource_url.split('.')
        ext = resource_url_parts[-1]
        if ext == 'gz':
            ext = resource_url_parts[-2]
        format = AUTO_FORMATS.get(ext)
        if format is None:
            raise ValueError('Could not determine format for %s based '
                             'on its file\nextension; use the "format" '
                             'argument to specify the format explicitly.'
                             % resource_url)

    if format not in FORMATS:
        raise ValueError('Unknown format type: %s!' % (format,))

    # If we've cached the resource, then just return it.
    if cache:
        resource_val = _resource_cache.get((resource_url, format))
        if resource_val is not None:
            if verbose:
                print('<<Using cached copy of %s>>' % (resource_url,))
            return resource_val

    # Let the user know what's going on.
    if verbose:
        print('<<Loading %s>>' % (resource_url,))

    # Load the resource.
    opened_resource = _open(resource_url)

    if format == 'raw':
        resource_val = opened_resource.read()
    elif format == 'pickle':
        resource_val = pickle.load(opened_resource)
    elif format == 'json':
        import json
        from nltk.jsontags import json_tags
        resource_val = json.load(opened_resource)
        tag = None
        if len(resource_val) != 1:
            tag = next(resource_val.keys())
        if tag not in json_tags:
            raise ValueError('Unknown json tag.')
    elif format == 'yaml':
        import yaml
        resource_val = yaml.load(opened_resource)
    else:
        # The resource is a text format.
        binary_data = opened_resource.read()
        if encoding is not None:
            string_data = binary_data.decode(encoding)
        else:
            try:
                string_data = binary_data.decode('utf-8')
            except UnicodeDecodeError:
                string_data = binary_data.decode('latin-1')
        if format == 'text':
            resource_val = string_data
        elif format == 'cfg':
            resource_val = nltk.grammar.CFG.fromstring(
                string_data, encoding=encoding)
        elif format == 'pcfg':
            resource_val = nltk.grammar.PCFG.fromstring(
                string_data, encoding=encoding)
        elif format == 'fcfg':
            resource_val = nltk.grammar.FeatureGrammar.fromstring(
                string_data, logic_parser=logic_parser,
                fstruct_reader=fstruct_reader, encoding=encoding)
        elif format == 'fol':
            resource_val = nltk.sem.read_logic(
                string_data, logic_parser=nltk.sem.logic.LogicParser(),
                encoding=encoding)
        elif format == 'logic':
            resource_val = nltk.sem.read_logic(
                string_data, logic_parser=logic_parser, encoding=encoding)
        elif format == 'val':
            resource_val = nltk.sem.read_valuation(
                string_data, encoding=encoding)
        else:
            raise AssertionError("Internal NLTK error: Format %s isn't "
                                 "handled by nltk.data.load()" % (format,))

    opened_resource.close()

    # If requested, add it to the cache.
    if cache:
        try:
            _resource_cache[(resource_url, format)] = resource_val
            # TODO: add this line
            # print('<<Caching a copy of %s>>' % (resource_url,))
        except TypeError:
            # We can't create weak references to some object types, like
            # strings and tuples.  For now, just don't cache them.
            pass

    return resource_val


def show_cfg(resource_url, escape='##'):
    """
    Write out a grammar file, ignoring escaped and empty lines.

    :type resource_url: str
    :param resource_url: A URL specifying where the resource should be
        loaded from.  The default protocol is "nltk:", which searches
        for the file in the the NLTK data package.
    :type escape: str
    :param escape: Prepended string that signals lines to be ignored
    """
    resource_url = normalize_resource_url(resource_url)
    resource_val = load(resource_url, format='text', cache=False)
    lines = resource_val.splitlines()
    for l in lines:
        if l.startswith(escape):
            continue
        if re.match('^$', l):
            continue
        print(l)


def clear_cache():
    """
    Remove all objects from the resource cache.
    :see: load()
    """
    _resource_cache.clear()


def _open(resource_url):
    """
    Helper function that returns an open file object for a resource,
    given its resource URL.  If the given resource URL uses the "nltk:"
    protocol, or uses no protocol, then use ``nltk.data.find`` to find
    its path, and open it with the given mode; if the resource URL
    uses the 'file' protocol, then open the file with the given mode;
    otherwise, delegate to ``urllib2.urlopen``.

    :type resource_url: str
    :param resource_url: A URL specifying where the resource should be
        loaded from.  The default protocol is "nltk:", which searches
        for the file in the the NLTK data package.
    """
    resource_url = normalize_resource_url(resource_url)
    protocol, path_ = split_resource_url(resource_url)

    if protocol is None or protocol.lower() == 'nltk':
        return find(path_, path + ['']).open()
    elif protocol.lower() == 'file':
        # urllib might not use mode='rb', so handle this one ourselves:
        return find(path_, ['']).open()
    else:
        return urlopen(resource_url)

######################################################################
# Lazy Resource Loader
######################################################################

# We shouldn't apply @python_2_unicode_compatible
# decorator to LazyLoader, this is resource.__class__ responsibility.


class LazyLoader(object):

    @py3_data
    def __init__(self, _path):
        self._path = _path

    def __load(self):
        resource = load(self._path)
        # This is where the magic happens!  Transform ourselves into
        # the object by modifying our own __dict__ and __class__ to
        # match that of `resource`.
        self.__dict__ = resource.__dict__
        self.__class__ = resource.__class__

    def __getattr__(self, attr):
        self.__load()
        # This looks circular, but its not, since __load() changes our
        # __class__ to something new:
        return getattr(self, attr)

    def __repr__(self):
        self.__load()
        # This looks circular, but its not, since __load() changes our
        # __class__ to something new:
        return repr(self)

######################################################################
# Open-On-Demand ZipFile
######################################################################


class OpenOnDemandZipFile(zipfile.ZipFile):
    """
    A subclass of ``zipfile.ZipFile`` that closes its file pointer
    whenever it is not using it; and re-opens it when it needs to read
    data from the zipfile.  This is useful for reducing the number of
    open file handles when many zip files are being accessed at once.
    ``OpenOnDemandZipFile`` must be constructed from a filename, not a
    file-like object (to allow re-opening).  ``OpenOnDemandZipFile`` is
    read-only (i.e. ``write()`` and ``writestr()`` are disabled.
    """
    @py3_data
    def __init__(self, filename):
        if not isinstance(filename, string_types):
            raise TypeError('ReopenableZipFile filename must be a string')
        zipfile.ZipFile.__init__(self, filename)
        assert self.filename == filename
        self.close()
        # After closing a ZipFile object, the _fileRefCnt needs to be cleared
        # for Python2and3 compatible code.
        self._fileRefCnt = 0

    def read(self, name):
        assert self.fp is None
        self.fp = open(self.filename, 'rb')
        value = zipfile.ZipFile.read(self, name)
        # Ensure that _fileRefCnt needs to be set for Python2and3 compatible code.
        # Since we only opened one file here, we add 1.
        self._fileRefCnt += 1
        self.close()
        return value

    def write(self, *args, **kwargs):
        """:raise NotImplementedError: OpenOnDemandZipfile is read-only"""
        raise NotImplementedError('OpenOnDemandZipfile is read-only')

    def writestr(self, *args, **kwargs):
        """:raise NotImplementedError: OpenOnDemandZipfile is read-only"""
        raise NotImplementedError('OpenOnDemandZipfile is read-only')

    def __repr__(self):
        return repr(str('OpenOnDemandZipFile(%r)') % self.filename)


__all__ = ['path', 'PathPointer', 'FileSystemPathPointer', 'BufferedGzipFile',
           'GzipFileSystemPathPointer', 'GzipFileSystemPathPointer',
           'find', 'retrieve', 'FORMATS', 'AUTO_FORMATS', 'load',
           'show_cfg', 'clear_cache', 'LazyLoader', 'OpenOnDemandZipFile',
           'GzipFileSystemPathPointer']
