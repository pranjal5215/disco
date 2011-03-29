"""
:mod:`disco.util` -- Helper functions
=====================================

This module provides utility functions that are mostly used by Disco
internally.

.. deprecated:: 0.4
                :func:`disco.util.data_err`, :func:`disco.util.err`, and :func:`disco.util.msg`
                will be removed completely in the next release,
                in favor of using normal Python **raise** and **print** statements.
"""
import os, sys
import cPickle, marshal, time, gzip
import copy_reg, functools

from cStringIO import StringIO
from itertools import chain, groupby, repeat
from types import CodeType, FunctionType
from urllib import urlencode

from disco.error import DiscoError, DataError, CommError
from disco.events import Message
from disco.settings import DiscoSettings

class MessageWriter(object):
    @classmethod
    def force_utf8(cls, string):
        if isinstance(string, unicode):
            return string.encode('utf-8', 'replace')
        return string.decode('utf-8', 'replace').encode('utf-8')

    def write(self, string):
        Message(self.force_utf8(string.strip())).send()

class netloc(tuple):
    @classmethod
    def parse(cls, netlocstr):
        netlocstr = netlocstr.split('@', 1)[1] if '@' in netlocstr else netlocstr
        if ':' in netlocstr:
            return cls(netlocstr.split(':'))
        return cls((netlocstr, ''))

    @property
    def host(self):
        return self[0]

    @property
    def port(self):
        return self[1]

    def __nonzero__((host, port)):
        return bool(host)

    def __str__((host, port)):
        return '%s:%s' % (host, port) if port else host

def chainify(iterable):
    return list(chain(*iterable))

def flatten(iterable):
    for item in iterable:
        if isiterable(item):
            for subitem in flatten(item):
                yield subitem
        else:
            yield item

def hexhash(string):
    from hashlib import md5
    return md5(string).hexdigest()[:2]

def isiterable(object):
    return hasattr(object, '__iter__')

def iskv(object):
    return isinstance(object, tuple) and len(object) is 2

def iterify(object):
    if isiterable(object):
        return object
    return repeat(object, 1)

def ilen(iter):
    return sum(1 for _ in iter)

def key((k, v)):
    return k

def kvgroup(kviter):
    """
    Group the values of consecutive keys which compare equal.

    Takes an iterator over ``k, v`` pairs,
    and returns an iterator over ``k, vs``.
    Does not sort the input first.
    """
    for k, kvs in groupby(kviter, key):
        yield k, (v for _k, v in kvs)

def kvify(entry):
    return entry if iskv(entry) else (entry, None)

def listify(object):
    return list(iterify(object))

def modulify(module):
    if isinstance(module, basestring):
        __import__(module)
        module = sys.modules[module]
    return module

def partition(iterable, fn):
    t, f = [], []
    for item in iterable:
        (t if fn(item) else f).append(item)
    return t, f

def reify(dotted_name, globals=globals()):
    if '.' in dotted_name:
        package, name = dotted_name.rsplit('.', 1)
        return getattr(__import__(package, fromlist=[name]), name)
    return eval(dotted_name, globals)

def shuffled(object):
    from random import shuffle
    shuffled = listify(object)
    shuffle(shuffled)
    return shuffled

def argcount(object):
    if hasattr(object, 'func_code'):
        return object.func_code.co_argcount
    argcount = object.func.func_code.co_argcount
    return argcount - len(object.args or ()) - len(object.keywords or ())

def globalize(object, globals):
    if isinstance(object, functools.partial):
        object = object.func
    if hasattr(object, 'func_globals'):
        for k, v in globals.iteritems():
            object.func_globals.setdefault(k, v)

def unpickle_partial(func, args, kwargs):
    return functools.partial(unpack(func),
                             *[unpack(x) for x in args],
                             **dict((k, unpack(v)) for k, v in kwargs))

def pickle_partial(p):
    kw = p.keywords or {}
    return unpickle_partial, (pack(p.func),
                              [pack(x) for x in p.args],
                              [(k, pack(v)) for k, v in kw.iteritems()])

# support functools.partial also on Pythons prior to 3.1
if sys.version_info < (3,1):
    copy_reg.pickle(functools.partial, pickle_partial)
copy_reg.pickle(FunctionType, lambda func: (unpack, (pack(func),)))

def pack(object):
    if hasattr(object, 'func_code'):
        if object.func_closure != None:
            raise TypeError("Function must not have closures: "
                            "%s (try using functools.partial instead)"
                            % object.func_name)
        return marshal.dumps((object.func_code, object.func_defaults))
    if isinstance(object, (list, tuple)):
        object = type(object)(pack(o) for o in object)
    return cPickle.dumps(object, cPickle.HIGHEST_PROTOCOL)

def unpack(string, globals={'__builtins__': __builtins__}):
    try:
        object = cPickle.loads(string)
        if isinstance(object, (list, tuple)):
            return type(object)(unpack(s, globals=globals) for s in object)
        return object
    except Exception:
        try:
            code, defs = marshal.loads(string)
            return FunctionType(code, globals, argdefs=defs)
        except Exception, e:
            raise ValueError("Could not unpack: %s (%s)" % (string, e))

def urljoin((scheme, netloc, path)):
    return '%s%s%s' % ('%s://' % scheme if scheme else '',
                       '%s/' % (netloc, ) if netloc else '',
                       path)

def schemesplit(url):
    return url.split('://', 1) if '://' in url else ('', url)

def urlsplit(url, localhost=None, settings=DiscoSettings()):
    scheme, rest = schemesplit(url)
    locstr, path = rest.split('/', 1)  if '/'   in rest else (rest ,'')
    disco_port = str(settings['DISCO_PORT'])
    host, port = netloc.parse(locstr)
    if scheme == 'disco' or port == disco_port:
        prefix, fname = path.split('/', 1)
        if localhost == True or locstr == localhost:
            scheme = 'file'
            if prefix == 'ddfs':
                path = os.path.join(settings['DDFS_ROOT'], fname)
            if prefix == 'disco':
                path = os.path.join(settings['DISCO_DATA'], fname)
        elif scheme == 'disco':
            scheme = 'http'
            locstr = '%s:%s' % (host, disco_port)
    if scheme == 'tag':
        if not path:
            path, locstr = locstr, ''
    return scheme, netloc.parse(locstr), path

def urlresolve(url, settings=DiscoSettings()):
    def master((host, port)):
        if not host:
            return settings['DISCO_MASTER']
        if not port:
            return 'disco://%s' % host
        return 'http://%s:%s' % (host, port)
    scheme, netloc, path = urlsplit(url)
    if scheme == 'dir':
        return urlresolve('%s/%s' % (master(netloc), path))
    if scheme == 'tag':
        return urlresolve('%s/ddfs/tag/%s' % (master(netloc), path))
    return '%s://%s/%s' % (scheme, netloc, path)

def urltoken(url):
    _scheme, rest = schemesplit(url)
    locstr, _path = rest.split('/', 1)  if '/'   in rest else (rest ,'')
    if '@' in locstr:
        auth = locstr.split('@', 1)[0]
        return auth.split(':')[1] if ':' in auth else auth

def msg(message):
    """
    .. deprecated:: 0.4 use **print** instead.

    Sends the string *message* to the master for logging. The message is
    shown on the web interface. To prevent a rogue job from overwhelming the
    master, the maximum *message* size is set to 255 characters and job is
    allowed to send at most 10 messages per second.
    """
    return Message(message).send()

def err(message):
    """
    .. deprecated:: 0.4
                    raise :class:`disco.error.DiscoError` instead.

    Raises a :class:`disco.error.DiscoError`. This terminates the job.
    """
    raise DiscoError(message)

def data_err(message, url):
    """
    .. deprecated:: 0.4
                    raise :class:`disco.error.DataError` instead.

    Raises a :class:`disco.error.DataError`.
    A data error should only be raised if it is likely that the error is transient.
    Typically this function is used by map readers to signal a temporary failure
    in accessing an input file.
    """
    raise DataError(message, url)

def jobname(url):
    """
    Extracts the job name from *url*.

    This function is particularly useful for using the methods in
    :class:`disco.core.Disco` given only the results of a job.
    A typical case is that you no longer need the results.
    You can tell Disco to delete the unneeded data as follows::

        from disco.core import Disco
        from disco.util import jobname

        Disco().purge(jobname(results[0]))

    """
    scheme, x, path = urlsplit(url)
    if scheme in ('disco', 'dir', 'http'):
        return path.strip('/').split('/')[-2]
    raise DiscoError("Cannot parse jobname from %s" % url)

def external(files):
    from disco.worker.classic.external import package
    return package(files)

def parse_dir(dir, partition=None):
    """
    Translates a directory URL (``dir://...``) to a list of normal URLs.

    This function might be useful for other programs that need to parse
    results returned by :meth:`disco.core.Disco.wait`, for instance.

    :param dir: a directory url, such as ``dir://nx02/test_simple@12243344``
    """
    # XXX: guarantee indices are read in the same order (task/labels) (for redundancy)
    return [url for id, url in sorted(read_index(dir)) if partition in (None, id)]

def proxy_url(url, proxy=DiscoSettings()['DISCO_PROXY']):
    if proxy:
        scheme, (host, port), path = urlsplit(url)
        return '%s/disco/node/%s/%s' % (proxy, host, path)
    return url

def read_index(dir):
    from disco.comm import open_url
    body, size, url = open_url(proxy_url(dir))
    if dir.endswith(".gz"):
        body = gzip.GzipFile(fileobj=body)
    for line in body:
        yield line.split()

def ispartitioned(input):
    if isiterable(input):
        return all(ispartitioned(i) for i in input) and len(input)
    return input.startswith('dir://')

def inputexpand(input, partition=None, settings=DiscoSettings()):
    from disco.ddfs import DDFS, istag
    if ispartitioned(input) and partition is not False:
        return zip(*(parse_dir(i, partition=partition) for i in iterify(input)))
    if isiterable(input):
        return [inputlist(input, partition=partition, settings=settings)]
    if istag(input):
        ddfs = DDFS(settings=settings)
        return chainify(blobs for name, tags, blobs in ddfs.findtags(input))
    return [input]

def inputlist(inputs, **kwargs):
    return filter(None, chainify(inputexpand(input, **kwargs) for input in inputs))

def save_oob(host, name, key, value, ddfs_token=None):
    from disco.ddfs import DDFS
    DDFS(host).push(DDFS.job_oob(name), [(StringIO(value), key)], delayed=True)

def load_oob(host, name, key):
    from disco.ddfs import DDFS
    # NB: this assumes that blobs are listed in LIFO order.
    # We want to return the latest version
    for fd, sze, url in DDFS(host).pull(DDFS.job_oob(name),
                                        blobfilter=lambda x: x == key):
        return fd.read()

def format_size(num):
    for unit in [' bytes','KB','MB','GB','TB']:
        if num < 1024.:
            return "%3.1f%s" % (num, unit)
        num /= 1024.
