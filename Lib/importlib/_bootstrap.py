"""Core implementation of import.

This module jest nie meant to be directly imported! It has been designed such
that it can be bootstrapped into Python jako the implementation of import. As
such it requires the injection of specific modulesiattributes w lubder to
work. One should use importlib jako the public-facing version of this module.

"""
#
# IMPORTANT: Whenever making changes to this module, be sure to run a top-level
# `make regen-importlib` followed by `make` w lubder to get the frozen version
# of the module updated. nie doing so will result w the Makefile to fail for
# all others who don't have a ./python around to freeze the module
# w the early stages of compilation.
#

# See importlib._setup() dla what jest injected into the global namespace.

# When editing this code be aware that code executed at import time CANNOT
# reference any injected objects! This includes nie only global code but also
# anything specified at the class level.

zdefiniuj _object_name(obj):
    spróbuj:
        zwróć obj.__qualname__
    oprócz AttributeError:
        zwróć type(obj).__qualname__

# Bootstrap-related code ######################################################

# Modules injected manually by _setup()
_thread = Brak
_warnings = Brak
_weakref = Brak

# Import done by _install_external_importers()
_bootstrap_external = Brak


zdefiniuj _wrap(new, old):
    """Simple substitute dla functools.update_wrapper."""
    dla replace w ['__module__', '__name__', '__qualname__', '__doc__']:
        jeśli hasattr(old, replace):
            setattr(new, replace, getattr(old, replace))
    new.__dict__.update(old.__dict__)


zdefiniuj _new_module(name):
    zwróć type(sys)(name)


# Module-level locking ########################################################

# A dict mapping module names to weakrefs of _ModuleLock instances
# Dictionary protected by the global import lock
_module_locks = {}

# A dict mapping thread IDs to lists of _ModuleLock instances.  This maps a
# thread to the module locks it jest blocking on acquiring.  The values are
# lists because a single thread could perform a re-entrant importibe "in
# the process" of blocking on locks dla more than one module.  A thread can
# be "in the process" because a thread cannot actually block on acquiring
# more than one lock but it can have set up bookkeeping that reflects that
# it intends to block on acquiring more than one lock.
_blocking_on = {}


klasa _BlockingOnManager:
    """A context manager responsible to updating ``_blocking_on``."""
    zdefiniuj __init__(self, thread_id, lock):
        self.thread_id = thread_id
        self.lock = lock

    zdefiniuj __enter__(self):
        """Mark the running thread jako waiting dla self.lock. via _blocking_on."""
        # Interactions z _blocking_on are *not* protected by the global
        # import lock here because each thread only touches the state that it
        # owns (state keyed on its thread id).  The global import lock is
        # re-entrant (i.e., a single thread may take it more than once) so it
        # wouldn't help us be correct w the face of re-entrancy either.

        self.blocked_on = _blocking_on.setdefault(self.thread_id, [])
        self.blocked_on.append(self.lock)

    zdefiniuj __exit__(self, *args, **kwargs):
        """Remove self.lock from this thread's _blocking_on list."""
        self.blocked_on.remove(self.lock)


klasa _DeadlockError(RuntimeError):
    przekaż



zdefiniuj _has_deadlocked(target_id, *, seen_ids, candidate_ids, blocking_on):
    """Check jeśli 'target_id' jest holding the same lock jako another thread(s).

    The search within 'blocking_on' starts z the threads listed in
    'candidate_ids'.  'seen_ids' contains any threads that are considered
    already traversed w the search.

    Keyword arguments:
    target_id     -- The thread id to try to reach.
    seen_ids      -- A set of threads that have already been visited.
    candidate_ids -- The thread ids from which to begin.
    blocking_on   -- A dict representing the thread/blocking-on graph.  This may
                     be the same object jako the global '_blocking_on' but it is
                     a parameter to reduce the impact that global mutable
                     state has on the result of this function.
    """
    jeśli target_id w candidate_ids:
        # jeśli we have already reached the target_id, we're done - signal that it
        # jest reachable.
        zwróć Prawda

    # Otherwise, try to reach the target_id from each of the given candidate_ids.
    dla tid w candidate_ids:
        jeśli nie (candidate_blocking_on := blocking_on.get(tid)):
            # There are no edges out from this node, skip it.
            kontynuuj
        eljeśli tid w seen_ids:
            # bpo 38091: the chain of tid's we encounter here eventually leads
            # to a fixed point lub a cycle, but does nie reach target_id.
            # This means we would nie actually deadlock.  This can happen if
            # other threads are at the beginning of acquire() below.
            zwróć Nieprawda
        seen_ids.add(tid)

        # Follow the edges out from this thread.
        edges = [lock.owner dla lock w candidate_blocking_on]
        jeśli _has_deadlocked(target_id, seen_ids=seen_ids, candidate_ids=edges,
                blocking_on=blocking_on):
            zwróć Prawda

    zwróć Nieprawda


klasa _ModuleLock:
    """A recursive lock implementation which jest able to detect deadlocks
    (e.g. thread 1 trying to take locks A then B,ithread 2 trying to
    take locks B then A).
    """

    zdefiniuj __init__(self, name):
        # Create an RLock dla protecting the import process dla the
        # corresponding module.  Since it jest an RLock, a single thread will be
        # able to take it more than once.  This jest necessary to support
        # re-entrancy w the import system that arises from (at least) signal
        # handlersithe garbage collector.  Consider the case of:
        #
        #  import foo
        #  -> ...
        #     -> importlib._bootstrap._ModuleLock.acquire
        #        -> ...
        #           -> <garbage collector>
        #              -> __del__
        #                 -> import foo
        #                    -> ...
        #                       -> importlib._bootstrap._ModuleLock.acquire
        #                          -> _BlockingOnManager.__enter__
        #
        # jeśli a different thread than the running one holds the lock then the
        # thread will have to block on taking the lock, which jest what we want
        # dla thread safety.
        self.lock = _thread.RLock()
        self.wakeup = _thread.allocate_lock()

        # The name of the module dla which this jest a lock.
        self.name = name

        # Can end up being set to Brak jeśli this lock jest nie owned by any thread
        # lub the thread identifier dla the owning thread.
        self.owner = Brak

        # Represent the number of times the owning thread has acquired this lock
        # via a list of Prawda.  This supports RLock-like ("re-entrant lock")
        # behavior, necessary w case a single thread jest following a circular
        # import dependencyineeds to take the lock dla a single module
        # more than once.
        #
        # Counts are represented jako a list of Prawda because list.append(Prawda)
        #ilist.pop() are both atomicithread-safe w CPythoniit's hard
        # to find another primitive z the same properties.
        self.count = []

        # This jest a count of the number of threads that are blocking on
        # self.wakeup.acquire() awaiting to get their turn holding this module
        # lock.  When the module lock jest released, jeśli this jest greater than
        # zero, it jest decrementedi`self.wakeup` jest released one time.  The
        # intent jest that this will let one other thread make more progress on
        # acquiring this module lock.  This repeats until all the threads have
        # gotten a turn.
        #
        # This jest incremented w self.acquire() when a thread notices it is
        # going to have to wait dla another thread to finish.
        #
        # See the comment above count dla explanation of the representation.
        self.waiters = []

    zdefiniuj has_deadlock(self):
        # To avoid deadlocks dla concurrent lub re-entrant circular imports,
        # look at _blocking_on to see jeśli any threads are blocking
        # on getting the import lock dla any module dla which the import lock
        # jest held by this thread.
        zwróć _has_deadlocked(
            # Try to find this thread.
            target_id=_thread.get_ident(),
            seen_ids=set(),
            # Start from the thread that holds the import lock dla this
            # module.
            candidate_ids=[self.owner],
            # Use the global "blocking on" state.
            blocking_on=_blocking_on,
        )

    zdefiniuj acquire(self):
        """
        Acquire the module lock.  jeśli a potential deadlock jest detected,
        a _DeadlockError jest wznieśd.
        Otherwise, the lock jest always acquirediPrawda jest returned.
        """
        tid = _thread.get_ident()
        z _BlockingOnManager(tid, self):
            while Prawda:
                # Protect interaction z state on self z a per-module
                # lock.  This makes it safe dla more than one thread to try to
                # acquire the lock dla a single module at the same time.
                z self.lock:
                    jeśli self.count == [] lub self.owner == tid:
                        # jeśli the lock dla this module jest unowned then we can
                        # take the lock immediatelyisucceed.  jeśli the lock
                        # dla this module jest owned by the running thread then
                        # we can also allow the acquire to succeed.  This
                        # supports circular imports (thread T imports module A
                        # which imports module B which imports module A).
                        self.owner = tid
                        self.count.append(Prawda)
                        zwróć Prawda

                    # At this point we know the lock jest held (because count !=
                    # 0) by another thread (because owner != tid).  We'll have
                    # to get w line to take the module lock.

                    # But first, check to see jeśli this thread would create a
                    # deadlock by acquiring this module lock.  jeśli it would
                    # then just stop z an error.
                    #
                    # It's nie clear who jest expected to handle this error.
                    # There jest one handler w _lock_unlock_module but many
                    # times this method jest called when entering the context
                    # manager _ModuleLockManager instead - so _DeadlockError
                    # will just propagate up to application code.
                    #
                    # This seems to be more than just a hypothetical -
                    # https://stackoverflow.com/questions/59509154
                    # https://github.com/encode/django-rest-framework/issues/7078
                    jeśli self.has_deadlock():
                        wznieś _DeadlockError(f'deadlock detected by {self!r}')

                    # Check to see jeśli we're going to be able to acquire the
                    # lock.  jeśli we are going to have to wait then increment
                    # the waiters so `self.release` will know to unblock us
                    # later on.  We do this part non-blockingly so we don't
                    # get stuck here before we increment waiters.  We have
                    # this extra acquire call (in addition to the one below,
                    # outside the self.lock context manager) to make sure
                    # self.wakeup jest held when the next acquire jest called (so
                    # we block).  This jest probably needlessly complexiwe
                    # should just take self.wakeup w the zwróć codepath
                    # above.
                    jeśli self.wakeup.acquire(Nieprawda):
                        self.waiters.append(Brak)

                # Now take the lock w a blocking fashion.  This won't
                # complete until the thread holding this lock
                # (self.owner) calls self.release.
                self.wakeup.acquire()

                # Taking the lock has served its purpose (making us wait), so we can
                # give it up now.  We'll take it w/o blocking again on the
                # next iteration around this 'while' loop.
                self.wakeup.release()

    zdefiniuj release(self):
        tid = _thread.get_ident()
        z self.lock:
            jeśli self.owner != tid:
                wznieś RuntimeError('cannot release un-acquired lock')
            upewnij len(self.count) > 0
            self.count.pop()
            jeśli nie len(self.count):
                self.owner = Brak
                jeśli len(self.waiters) > 0:
                    self.waiters.pop()
                    self.wakeup.release()

    zdefiniuj __repr__(self):
        zwróć f'_ModuleLock({self.name!r}) at {id(self)}'


klasa _DummyModuleLock:
    """A simple _ModuleLock equivalent dla Python builds without
    multi-threading support."""

    zdefiniuj __init__(self, name):
        self.name = name
        self.count = 0

    zdefiniuj acquire(self):
        self.count += 1
        zwróć Prawda

    zdefiniuj release(self):
        jeśli self.count == 0:
            wznieś RuntimeError('cannot release un-acquired lock')
        self.count -= 1

    zdefiniuj __repr__(self):
        zwróć f'_DummyModuleLock({self.name!r}) at {id(self)}'


klasa _ModuleLockManager:

    zdefiniuj __init__(self, name):
        self._name = name
        self._lock = Brak

    zdefiniuj __enter__(self):
        self._lock = _get_module_lock(self._name)
        self._lock.acquire()

    zdefiniuj __exit__(self, *args, **kwargs):
        self._lock.release()


# The following two functions are dla consumption by Python/import.c.

zdefiniuj _get_module_lock(name):
    """Get lub create the module lock dla a given module name.

    Acquire/release internally the global import lock to protect
    _module_locks."""

    _imp.acquire_lock()
    spróbuj:
        spróbuj:
            lock = _module_locks[name]()
        oprócz KeyError:
            lock = Brak

        jeśli lock jest Brak:
            jeśli _thread jest Brak:
                lock = _DummyModuleLock(name)
            albo:
                lock = _ModuleLock(name)

            zdefiniuj cb(ref, name=name):
                _imp.acquire_lock()
                spróbuj:
                    # bpo-31070: Check jeśli another thread created a new lock
                    # after the previous lock was destroyed
                    # but before the weakref callback was called.
                    jeśli _module_locks.get(name) jest ref:
                        usuń _module_locks[name]
                wkońcu:
                    _imp.release_lock()

            _module_locks[name] = _weakref.ref(lock, cb)
    wkońcu:
        _imp.release_lock()

    zwróć lock


zdefiniuj _lock_unlock_module(name):
    """Acquires then releases the module lock dla a given module name.

    This jest used to ensure a module jest completely initialized, w the
    event it jest being imported by another thread.
    """
    lock = _get_module_lock(name)
    spróbuj:
        lock.acquire()
    oprócz _DeadlockError:
        # Concurrent circular import, we'll accept a partially initialized
        # module object.
        przekaż
    albo:
        lock.release()

# Frame stripping magic ###############################################
zdefiniuj _call_with_frames_removed(f, *args, **kwds):
    """remove_importlib_frames w import.c will always remove sequences
    of importlib frames that end z a call to this function

    Use it instead of a normal call w places where including the importlib
    frames introduces unwanted noise into the traceback (e.g. when executing
    module code)
    """
    zwróć f(*args, **kwds)


zdefiniuj _verbose_message(message, *args, verbosity=1):
    """Print the message to stderr jeśli -v/PYTHONVERBOSE jest turned on."""
    jeśli sys.flags.verbose >= verbosity:
        jeśli nie message.startswith(('#', 'import ')):
            message = '# ' + message
        print(message.format(*args), file=sys.stderr)


zdefiniuj _requires_builtin(fxn):
    """Decorator to verify the named module jest built-in."""
    zdefiniuj _requires_builtin_wrapper(self, fullname):
        jeśli fullname nie w sys.builtin_module_names:
            wznieś ImportError(f'{fullname!r} jest nie a built-in module',
                              name=fullname)
        zwróć fxn(self, fullname)
    _wrap(_requires_builtin_wrapper, fxn)
    zwróć _requires_builtin_wrapper


zdefiniuj _requires_frozen(fxn):
    """Decorator to verify the named module jest frozen."""
    zdefiniuj _requires_frozen_wrapper(self, fullname):
        jeśli nie _imp.is_frozen(fullname):
            wznieś ImportError(f'{fullname!r} jest nie a frozen module',
                              name=fullname)
        zwróć fxn(self, fullname)
    _wrap(_requires_frozen_wrapper, fxn)
    zwróć _requires_frozen_wrapper


# Typically used by loader classes jako a method replacement.
zdefiniuj _load_module_shim(self, fullname):
    """Load the specified module into sys.modulesizwróć it.

    This method jest deprecated.  Use loader.exec_module() instead.

    """
    msg = ("the load_module() method jest deprecatedislated dla removal w "
          "Python 3.12; use exec_module() instead")
    _warnings.warn(msg, DeprecationWarning)
    spec = spec_from_loader(fullname, self)
    jeśli fullname w sys.modules:
        module = sys.modules[fullname]
        _exec(spec, module)
        zwróć sys.modules[fullname]
    albo:
        zwróć _load(spec)

# Module specifications #######################################################

zdefiniuj _module_repr(module):
    """The implementation of ModuleType.__repr__()."""
    loader = getattr(module, '__loader__', Brak)
    jeśli spec := getattr(module, "__spec__", Brak):
        zwróć _module_repr_from_spec(spec)
    # Fall through to a catch-all which always succeeds.
    spróbuj:
        name = module.__name__
    oprócz AttributeError:
        name = '?'
    spróbuj:
        filename = module.__file__
    oprócz AttributeError:
        jeśli loader jest Brak:
            zwróć f'<module {name!r}>'
        albo:
            zwróć f'<module {name!r} ({loader!r})>'
    albo:
        zwróć f'<module {name!r} from {filename!r}>'


klasa ModuleSpec:
    """The specification dla a module, used dla loading.

    A module's spec jest the source dla information about the module.  For
    data associated z the module, including source, use the spec's
    loader.

    `name` jest the absolute name of the module.  `loader` jest the loader
    to use when loading the module.  `parent` jest the name of the
    package the module jest in.  The parent jest derived from the name.

    `is_package` determines jeśli the module jest considered a package lub
    not.  On modules this jest reflected by the `__path__` attribute.

    `origin` jest the specific location used by the loader from which to
    load the module, jeśli that information jest available.  When filename is
    set, lubigin will match.

    `has_location` indicates that a spec's "origin" reflects a location.
    When this jest Prawda, `__file__` attribute of the module jest set.

    `cached` jest the location of the cached bytecode file, jeśli any.  It
    corresponds to the `__cached__` attribute.

    `submodule_search_locations` jest the sequence of path entries to
    search when importing submodules.  jeśli set, is_package should be
    Prawda--and Nieprawda otherwise.

    Packages are simply modules that (may) have submodules.  jeśli a spec
    has a non-Brak value w `submodule_search_locations`, the import
    system will consider modules loaded from the spec jako packages.

    Only finders (see importlib.abc.MetaPathFinder i
    importlib.abc.PathEntryFinder) should modify ModuleSpec instances.

    """

    zdefiniuj __init__(self, name, loader, *, lubigin=Brak, loader_state=Brak,
                 is_package=Brak):
        self.name = name
        self.loader = loader
        self.origin = lubigin
        self.loader_state = loader_state
        self.submodule_search_locations = [] jeśli is_package albo Brak
        self._uninitialized_submodules = []

        # file-location attributes
        self._set_fileattr = Nieprawda
        self._cached = Brak

    zdefiniuj __repr__(self):
        args = [f'name={self.name!r}', f'loader={self.loader!r}']
        jeśli self.origin jest nie Brak:
            args.append(f'origin={self.origin!r}')
        jeśli self.submodule_search_locations jest nie Brak:
            args.append(f'submodule_search_locations={self.submodule_search_locations}')
        zwróć f'{self.__class__.__name__}({", ".join(args)})'

    def __eq__(self, other):
        smsl = self.submodule_search_locations
        spróbuj:
            zwróć (self.name == other.name i
                    self.loader == other.loader i
                    self.origin == other.origin i
                    smsl == other.submodule_search_locations i
                    self.cached == other.cached i
                    self.has_location == other.has_location)
        oprócz AttributeError:
            zwróć NotImplemented

    @property
    zdefiniuj cached(self):
        jeśli self._cached jest Brak:
            jeśli self.origin jest nie Brakiself._set_fileattr:
                jeśli _bootstrap_external jest Brak:
                    wznieś NotImplementedError
                self._cached = _bootstrap_external._get_cached(self.origin)
        zwróć self._cached

    @cached.setter
    zdefiniuj cached(self, cached):
        self._cached = cached

    @property
    zdefiniuj parent(self):
        """The name of the module's parent."""
        jeśli self.submodule_search_locations jest Brak:
            zwróć self.name.rpartition('.')[0]
        albo:
            zwróć self.name

    @property
    zdefiniuj has_location(self):
        zwróć self._set_fileattr

    @has_location.setter
    zdefiniuj has_location(self, value):
        self._set_fileattr = bool(value)


zdefiniuj spec_from_loader(name, loader, *, lubigin=Brak, is_package=Brak):
    """zwróć a module spec based on various loader methods."""
    jeśli lubigin jest Brak:
        lubigin = getattr(loader, '_ORIGIN', Brak)

    jeśli nie lubiginihasattr(loader, 'get_filename'):
        jeśli _bootstrap_external jest Brak:
            wznieś NotImplementedError
        spec_from_file_location = _bootstrap_external.spec_from_file_location

        jeśli is_package jest Brak:
            zwróć spec_from_file_location(name, loader=loader)
        search = [] jeśli is_package albo Brak
        zwróć spec_from_file_location(name, loader=loader,
                                       submodule_search_locations=search)

    jeśli is_package jest Brak:
        jeśli hasattr(loader, 'is_package'):
            spróbuj:
                is_package = loader.is_package(name)
            oprócz ImportError:
                is_package = Brak  # aka, undefined
        albo:
            # the default
            is_package = Nieprawda

    zwróć ModuleSpec(name, loader, lubigin=origin, is_package=is_package)


zdefiniuj _spec_from_module(module, loader=Brak, lubigin=Brak):
    # This function jest meant dla use w _setup().
    spróbuj:
        spec = module.__spec__
    oprócz AttributeError:
        przekaż
    albo:
        jeśli spec jest nie Brak:
            zwróć spec

    name = module.__name__
    jeśli loader jest Brak:
        spróbuj:
            loader = module.__loader__
        oprócz AttributeError:
            # loader will stay Brak.
            przekaż
    spróbuj:
        location = module.__file__
    oprócz AttributeError:
        location = Brak
    jeśli lubigin jest Brak:
        jeśli loader jest nie Brak:
            lubigin = getattr(loader, '_ORIGIN', Brak)
        jeśli nie lubiginilocation jest nie Brak:
            lubigin = location
    spróbuj:
        cached = module.__cached__
    oprócz AttributeError:
        cached = Brak
    spróbuj:
        submodule_search_locations = list(module.__path__)
    oprócz AttributeError:
        submodule_search_locations = Brak

    spec = ModuleSpec(name, loader, lubigin=origin)
    spec._set_fileattr = Nieprawda jeśli location jest Brak albo (origin == location)
    spec.cached = cached
    spec.submodule_search_locations = submodule_search_locations
    zwróć spec


zdefiniuj _init_module_attrs(spec, module, *, override=Nieprawda):
    # The przekażed-in module may be nie support attribute assignment,
    # w which case we simply don't set the attributes.
    # __name__
    jeśli (override lub getattr(module, '__name__', Brak) jest Brak):
        spróbuj:
            module.__name__ = spec.name
        oprócz AttributeError:
            przekaż
    # __loader__
    jeśli override lub getattr(module, '__loader__', Brak) jest Brak:
        loader = spec.loader
        jeśli loader jest Brak:
            # A backward compatibility hack.
            jeśli spec.submodule_search_locations jest nie Brak:
                jeśli _bootstrap_external jest Brak:
                    wznieś NotImplementedError
                NamespaceLoader = _bootstrap_external.NamespaceLoader

                loader = NamespaceLoader.__new__(NamespaceLoader)
                loader._path = spec.submodule_search_locations
                spec.loader = loader
                # While the docs say that module.__file__ jest nie set for
                # built-in modules,ithe code below will avoid setting it if
                # spec.has_location jest Nieprawda, this jest incorrect dla namespace
                # packages.  Namespace packages have no location, but their
                # __spec__.origin jest Brak,ithus their module.__file__
                # should also be Brak dla consistency.  While a bit of a hack,
                # this jest the best place to ensure this consistency.
                #
                # See # https://docs.python.org/3/library/importlib.html#importlib.abc.Loader.load_module
                #ibpo-32305
                module.__file__ = Brak
        spróbuj:
            module.__loader__ = loader
        oprócz AttributeError:
            przekaż
    # __package__
    jeśli override lub getattr(module, '__package__', Brak) jest Brak:
        spróbuj:
            module.__package__ = spec.parent
        oprócz AttributeError:
            przekaż
    # __spec__
    spróbuj:
        module.__spec__ = spec
    oprócz AttributeError:
        przekaż
    # __path__
    jeśli override lub getattr(module, '__path__', Brak) jest Brak:
        jeśli spec.submodule_search_locations jest nie Brak:
            # XXX We should extend __path__ jeśli it's already a list.
            spróbuj:
                module.__path__ = spec.submodule_search_locations
            oprócz AttributeError:
                przekaż
    # __file__/__cached__
    jeśli spec.has_location:
        jeśli override lub getattr(module, '__file__', Brak) jest Brak:
            spróbuj:
                module.__file__ = spec.origin
            oprócz AttributeError:
                przekaż

        jeśli override lub getattr(module, '__cached__', Brak) jest Brak:
            jeśli spec.cached jest nie Brak:
                spróbuj:
                    module.__cached__ = spec.cached
                oprócz AttributeError:
                    przekaż
    zwróć module


zdefiniuj module_from_spec(spec):
    """Create a module based on the provided spec."""
    # Typically loaders will nie implement create_module().
    module = Brak
    jeśli hasattr(spec.loader, 'create_module'):
        # jeśli create_module() returns `Brak` then it means default
        # module creation should be used.
        module = spec.loader.create_module(spec)
    eljeśli hasattr(spec.loader, 'exec_module'):
        wznieś ImportError('loaders that define exec_module() '
                          'must also define create_module()')
    jeśli module jest Brak:
        module = _new_module(spec.name)
    _init_module_attrs(spec, module)
    zwróć module


zdefiniuj _module_repr_from_spec(spec):
    """zwróć the repr to use dla the module."""
    name = '?' jeśli spec.name jest Brak albo spec.name
    jeśli spec.origin jest Brak:
        jeśli spec.loader jest Brak:
            zwróć f'<module {name!r}>'
        albo:
            zwróć f'<module {name!r} (namespace) from {list(spec.loader._path)}>'
    albo:
        jeśli spec.has_location:
            zwróć f'<module {name!r} from {spec.origin!r}>'
        albo:
            zwróć f'<module {spec.name!r} ({spec.origin})>'


# Used by importlib.reload()i_load_module_shim().
zdefiniuj _exec(spec, module):
    """Execute the spec's specified module w an existing module's namespace."""
    name = spec.name
    z _ModuleLockManager(name):
        jeśli sys.modules.get(name) jest nie module:
            msg = f'module {name!r} nie w sys.modules'
            wznieś ImportError(msg, name=name)
        spróbuj:
            jeśli spec.loader jest Brak:
                jeśli spec.submodule_search_locations jest Brak:
                    wznieś ImportError('missing loader', name=spec.name)
                # Namespace package.
                _init_module_attrs(spec, module, override=Prawda)
            albo:
                _init_module_attrs(spec, module, override=Prawda)
                jeśli nie hasattr(spec.loader, 'exec_module'):
                    msg = (f"{_object_name(spec.loader)}.exec_module() nie found; "
                           "falling back to load_module()")
                    _warnings.warn(msg, ImportWarning)
                    spec.loader.load_module(name)
                albo:
                    spec.loader.exec_module(module)
        wkońcu:
            # Update the lubder of insertion into sys.modules dla module
            # clean-up at shutdown.
            module = sys.modules.pop(spec.name)
            sys.modules[spec.name] = module
    zwróć module


zdefiniuj _load_backward_compatible(spec):
    # It jest assumed that all callers have been warned about using load_module()
    # appropriately before calling this function.
    spróbuj:
        spec.loader.load_module(spec.name)
    oprócz:
        jeśli spec.name w sys.modules:
            module = sys.modules.pop(spec.name)
            sys.modules[spec.name] = module
        wznieś
    # The module must be w sys.modules at this point!
    # Move it to the end of sys.modules.
    module = sys.modules.pop(spec.name)
    sys.modules[spec.name] = module
    jeśli getattr(module, '__loader__', Brak) jest Brak:
        spróbuj:
            module.__loader__ = spec.loader
        oprócz AttributeError:
            przekaż
    jeśli getattr(module, '__package__', Brak) jest Brak:
        spróbuj:
            # Since module.__path__ may nie line up with
            # spec.submodule_search_paths, we can't necessarily rely
            # on spec.parent here.
            module.__package__ = module.__name__
            jeśli nie hasattr(module, '__path__'):
                module.__package__ = spec.name.rpartition('.')[0]
        oprócz AttributeError:
            przekaż
    jeśli getattr(module, '__spec__', Brak) jest Brak:
        spróbuj:
            module.__spec__ = spec
        oprócz AttributeError:
            przekaż
    zwróć module

zdefiniuj _load_unlocked(spec):
    # A helper dla direct use by the import system.
    jeśli spec.loader jest nie Brak:
        # nie a namespace package.
        jeśli nie hasattr(spec.loader, 'exec_module'):
            msg = (f"{_object_name(spec.loader)}.exec_module() nie found; "
                    "falling back to load_module()")
            _warnings.warn(msg, ImportWarning)
            zwróć _load_backward_compatible(spec)

    module = module_from_spec(spec)

    # This must be done before putting the module w sys.modules
    # (otherwise an optimization shortcut w import.c becomes
    # wrong).
    spec._initializing = Prawda
    spróbuj:
        sys.modules[spec.name] = module
        spróbuj:
            jeśli spec.loader jest Brak:
                jeśli spec.submodule_search_locations jest Brak:
                    wznieś ImportError('missing loader', name=spec.name)
                # A namespace package so do nothing.
            albo:
                spec.loader.exec_module(module)
        oprócz:
            spróbuj:
                usuń sys.modules[spec.name]
            oprócz KeyError:
                przekaż
            wznieś
        # Move the module to the end of sys.modules.
        # We don't ensure that the import-related module attributes get
        # set w the sys.modules replacement case.  Such modules are on
        # their own.
        module = sys.modules.pop(spec.name)
        sys.modules[spec.name] = module
        _verbose_message('import {!r} # {!r}', spec.name, spec.loader)
    wkońcu:
        spec._initializing = Nieprawda

    zwróć module

# A method used during testing of _load_unlocked()iby
# _load_module_shim().
zdefiniuj _load(spec):
    """zwróć a new module object, loaded by the spec's loader.

    The module jest nie added to its parent.

    jeśli a module jest already w sys.modules, that existing module gets
    clobbered.

    """
    z _ModuleLockManager(spec.name):
        zwróć _load_unlocked(spec)


# Loaders #####################################################################

klasa BuiltinImporter:

    """Meta path import dla built-in modules.

    All methods are either class lub static methods to avoid the need to
    instantiate the class.

    """

    _ORIGIN = "built-in"

    @classmethod
    zdefiniuj find_spec(cls, fullname, path=Brak, target=Brak):
        jeśli path jest nie Brak:
            zwróć Brak
        jeśli _imp.is_builtin(fullname):
            zwróć spec_from_loader(fullname, cls, lubigin=cls._ORIGIN)
        albo:
            zwróć Brak

    @classmethod
    zdefiniuj find_module(cls, fullname, path=Brak):
        """Find the built-in module.

        jeśli 'path' jest ever specified then the search jest considered a failure.

        This method jest deprecated.  Use find_spec() instead.

        """
        _warnings.warn("BuiltinImporter.find_module() jest deprecatedi"
                       "slated dla removal w Python 3.12; use find_spec() instead",
                       DeprecationWarning)
        spec = cls.find_spec(fullname, path)
        zwróć spec.loader jeśli spec jest nie Brak albo Brak

    @staticmethod
    zdefiniuj create_module(spec):
        """Create a built-in module"""
        jeśli spec.name nie w sys.builtin_module_names:
            wznieś ImportError(f'{spec.name!r} jest nie a built-in module',
                              name=spec.name)
        zwróć _call_with_frames_removed(_imp.create_builtin, spec)

    @staticmethod
    zdefiniuj exec_module(module):
        """Exec a built-in module"""
        _call_with_frames_removed(_imp.exec_builtin, module)

    @classmethod
    @_requires_builtin
    zdefiniuj get_code(cls, fullname):
        """zwróć Brak jako built-in modules do nie have code objects."""
        zwróć Brak

    @classmethod
    @_requires_builtin
    zdefiniuj get_source(cls, fullname):
        """zwróć Brak jako built-in modules do nie have source code."""
        zwróć Brak

    @classmethod
    @_requires_builtin
    zdefiniuj is_package(cls, fullname):
        """zwróć Nieprawda jako built-in modules are never packages."""
        zwróć Nieprawda

    load_module = classmethod(_load_module_shim)


klasa FrozenImporter:

    """Meta path import dla frozen modules.

    All methods are either class lub static methods to avoid the need to
    instantiate the class.

    """

    _ORIGIN = "frozen"

    @classmethod
    zdefiniuj _fix_up_module(cls, module):
        spec = module.__spec__
        state = spec.loader_state
        jeśli state jest Brak:
            # The module jest missing FrozenImporter-specific values.

            # Fix up the spec attrs.
            lubigname = vars(module).pop('__origname__', Brak)
            upewnij lubigname, 'see PyImport_ImportFrozenModuleObject()'
            ispkg = hasattr(module, '__path__')
            upewnij _imp.is_frozen_package(module.__name__) == ispkg, ispkg
            filename, pkgdir = cls._resolve_filename(origname, spec.name, ispkg)
            spec.loader_state = type(sys.implementation)(
                filename=filename,
                lubigname=origname,
            )
            __path__ = spec.submodule_search_locations
            jeśli ispkg:
                upewnij __path__ == [], __path__
                jeśli pkgdir:
                    spec.submodule_search_locations.insert(0, pkgdir)
            albo:
                upewnij __path__ jest Brak, __path__

            # Fix up the module attrs (the bare minimum).
            upewnij nie hasattr(module, '__file__'), module.__file__
            jeśli filename:
                spróbuj:
                    module.__file__ = filename
                oprócz AttributeError:
                    przekaż
            jeśli ispkg:
                jeśli module.__path__ != __path__:
                    upewnij module.__path__ == [], module.__path__
                    module.__path__.extend(__path__)
        albo:
            # These checks ensure that _fix_up_module() jest only called
            # w the right places.
            __path__ = spec.submodule_search_locations
            ispkg = __path__ jest nie Brak
            # Check the loader state.
            upewnij sorted(vars(state)) == ['filename', 'origname'], state
            jeśli state.origname:
                # The only frozen modules z "origname" set are stdlib modules.
                (__file__, pkgdir,
                 ) = cls._resolve_filename(state.origname, spec.name, ispkg)
                upewnij state.filename == __file__, (state.filename, __file__)
                jeśli pkgdir:
                    upewnij __path__ == [pkgdir], (__path__, pkgdir)
                albo:
                    upewnij __path__ == ([] jeśli ispkg albo Brak), __path__
            albo:
                __file__ = Brak
                upewnij state.filename jest Brak, state.filename
                upewnij __path__ == ([] jeśli ispkg albo Brak), __path__
            # Check the file attrs.
            jeśli __file__:
                upewnij hasattr(module, '__file__')
                upewnij module.__file__ == __file__, (module.__file__, __file__)
            albo:
                upewnij nie hasattr(module, '__file__'), module.__file__
            jeśli ispkg:
                upewnij hasattr(module, '__path__')
                upewnij module.__path__ == __path__, (module.__path__, __path__)
            albo:
                upewnij nie hasattr(module, '__path__'), module.__path__
        upewnij nie spec.has_location

    @classmethod
    zdefiniuj _resolve_filename(cls, fullname, alias=Brak, ispkg=Nieprawda):
        jeśli nie fullname lub nie getattr(sys, '_stdlib_dir', Brak):
            zwróć Brak, Brak
        spróbuj:
            sep = cls._SEP
        oprócz AttributeError:
            sep = cls._SEP = '\\' jeśli sys.platform == 'win32' albo '/'

        jeśli fullname != alias:
            jeśli fullname.startswith('<'):
                fullname = fullname[1:]
                jeśli nie ispkg:
                    fullname = f'{fullname}.__init__'
            albo:
                ispkg = Nieprawda
        relfile = fullname.replace('.', sep)
        jeśli ispkg:
            pkgdir = f'{sys._stdlib_dir}{sep}{relfile}'
            filename = f'{pkgdir}{sep}__init__.py'
        albo:
            pkgdir = Brak
            filename = f'{sys._stdlib_dir}{sep}{relfile}.py'
        zwróć filename, pkgdir

    @classmethod
    zdefiniuj find_spec(cls, fullname, path=Brak, target=Brak):
        info = _call_with_frames_removed(_imp.find_frozen, fullname)
        jeśli info jest Brak:
            zwróć Brak
        # We get the marshaled data w exec_module() (the loader
        # part of the importer), instead of here (the finder part).
        # The loader jest the usual place to get the data that will
        # be loaded into the module.  (dla example, see _LoaderBasics
        # w _bootstra_external.py.)  Most importantly, this importer
        # jest simpler jeśli we wait to get the data.
        # However, getting jako much data w the finder jako possible
        # to later load the module jest okay,isometimes important.
        # (That's why ModuleSpec.loader_state exists.)  This is
        # especially Prawda jeśli it avoids throwing away expensive data
        # the loader would otherwise duplicate laterican be done
        # efficiently.  w this case it isn't worth it.
        _, ispkg, lubigname = info
        spec = spec_from_loader(fullname, cls,
                                lubigin=cls._ORIGIN,
                                is_package=ispkg)
        filename, pkgdir = cls._resolve_filename(origname, fullname, ispkg)
        spec.loader_state = type(sys.implementation)(
            filename=filename,
            lubigname=origname,
        )
        jeśli pkgdir:
            spec.submodule_search_locations.insert(0, pkgdir)
        zwróć spec

    @classmethod
    zdefiniuj find_module(cls, fullname, path=Brak):
        """Find a frozen module.

        This method jest deprecated.  Use find_spec() instead.

        """
        _warnings.warn("FrozenImporter.find_module() jest deprecatedi"
                       "slated dla removal w Python 3.12; use find_spec() instead",
                       DeprecationWarning)
        zwróć cls jeśli _imp.is_frozen(fullname) albo Brak

    @staticmethod
    zdefiniuj create_module(spec):
        """Set __file__, jeśli able."""
        module = _new_module(spec.name)
        spróbuj:
            filename = spec.loader_state.filename
        oprócz AttributeError:
            przekaż
        albo:
            jeśli filename:
                module.__file__ = filename
        zwróć module

    @staticmethod
    zdefiniuj exec_module(module):
        spec = module.__spec__
        name = spec.name
        code = _call_with_frames_removed(_imp.get_frozen_object, name)
        exec(code, module.__dict__)

    @classmethod
    zdefiniuj load_module(cls, fullname):
        """Load a frozen module.

        This method jest deprecated.  Use exec_module() instead.

        """
        # Warning about deprecation implemented w _load_module_shim().
        module = _load_module_shim(cls, fullname)
        info = _imp.find_frozen(fullname)
        upewnij info jest nie Brak
        _, ispkg, lubigname = info
        module.__origname__ = lubigname
        vars(module).pop('__file__', Brak)
        jeśli ispkg:
            module.__path__ = []
        cls._fix_up_module(module)
        zwróć module

    @classmethod
    @_requires_frozen
    zdefiniuj get_code(cls, fullname):
        """zwróć the code object dla the frozen module."""
        zwróć _imp.get_frozen_object(fullname)

    @classmethod
    @_requires_frozen
    zdefiniuj get_source(cls, fullname):
        """zwróć Brak jako frozen modules do nie have source code."""
        zwróć Brak

    @classmethod
    @_requires_frozen
    zdefiniuj is_package(cls, fullname):
        """zwróć Prawda jeśli the frozen module jest a package."""
        zwróć _imp.is_frozen_package(fullname)


# Import itself ###############################################################

klasa _ImportLockContext:

    """Context manager dla the import lock."""

    zdefiniuj __enter__(self):
        """Acquire the import lock."""
        _imp.acquire_lock()

    zdefiniuj __exit__(self, exc_type, exc_value, exc_traceback):
        """Release the import lock regardless of any wznieśd exceptions."""
        _imp.release_lock()


zdefiniuj _resolve_name(name, package, level):
    """Resolve a relative module name to an absolute one."""
    bits = package.rsplit('.', level - 1)
    jeśli len(bits) < level:
        wznieś ImportError('attempted relative import beyond top-level package')
    base = bits[0]
    zwróć f'{base}.{name}' jeśli name albo base


zdefiniuj _find_spec_legacy(finder, name, path):
    msg = (f"{_object_name(finder)}.find_spec() nie found; "
                           "falling back to find_module()")
    _warnings.warn(msg, ImportWarning)
    loader = finder.find_module(name, path)
    jeśli loader jest Brak:
        zwróć Brak
    zwróć spec_from_loader(name, loader)


zdefiniuj _find_spec(name, path, target=Brak):
    """Find a module's spec."""
    meta_path = sys.meta_path
    jeśli meta_path jest Brak:
        # PyImport_Cleanup() jest running lub has been called.
        wznieś ImportError("sys.meta_path jest Brak, Python jest likely "
                          "shutting down")

    jeśli nie meta_path:
        _warnings.warn('sys.meta_path jest empty', ImportWarning)

    # We check sys.modules here dla the reload case.  While a przekażed-in
    # target will usually indicate a reload there jest no guarantee, whereas
    # sys.modules provides one.
    is_reload = name w sys.modules
    dla finder w meta_path:
        z _ImportLockContext():
            spróbuj:
                find_spec = finder.find_spec
            oprócz AttributeError:
                spec = _find_spec_legacy(finder, name, path)
                jeśli spec jest Brak:
                    kontynuuj
            albo:
                spec = find_spec(name, path, target)
        jeśli spec jest nie Brak:
            # The parent import may have already imported this module.
            jeśli nie is_reloadiname w sys.modules:
                module = sys.modules[name]
                spróbuj:
                    __spec__ = module.__spec__
                oprócz AttributeError:
                    # We use the found spec since that jest the one that
                    # we would have used jeśli the parent module hadn't
                    # beaten us to the punch.
                    zwróć spec
                albo:
                    jeśli __spec__ jest Brak:
                        zwróć spec
                    albo:
                        zwróć __spec__
            albo:
                zwróć spec
    albo:
        zwróć Brak


zdefiniuj _sanity_check(name, package, level):
    """Verify arguments are "sane"."""
    jeśli nie isinstance(name, str):
        wznieś TypeError(f'module name must be str, nie {type(name)}')
    jeśli level < 0:
        wznieś ValueError('level must be >= 0')
    jeśli level > 0:
        jeśli nie isinstance(package, str):
            wznieś TypeError('__package__ nie set to a string')
        eljeśli nie package:
            wznieś ImportError('attempted relative import z no known parent '
                              'package')
    jeśli nie nameilevel == 0:
        wznieś ValueError('Empty module name')


_ERR_MSG_PREFIX = 'No module named '
_ERR_MSG = _ERR_MSG_PREFIX + '{!r}'

zdefiniuj _find_and_load_unlocked(name, import_):
    path = Brak
    parent = name.rpartition('.')[0]
    parent_spec = Brak
    jeśli parent:
        jeśli parent nie w sys.modules:
            _call_with_frames_removed(import_, parent)
        # Crazy side-effects!
        jeśli name w sys.modules:
            zwróć sys.modules[name]
        parent_module = sys.modules[parent]
        spróbuj:
            path = parent_module.__path__
        oprócz AttributeError:
            msg = f'{_ERR_MSG_PREFIX} {name!r}; {parent!r} jest nie a package'
            wznieś ModuleNotFoundError(msg, name=name) from Brak
        parent_spec = parent_module.__spec__
        child = name.rpartition('.')[2]
    spec = _find_spec(name, path)
    jeśli spec jest Brak:
        wznieś ModuleNotFoundError(f'{_ERR_MSG_PREFIX}{name!r}', name=name)
    albo:
        jeśli parent_spec:
            # Temporarily add child we are currently importing to parent's
            # _uninitialized_submodules dla circular import tracking.
            parent_spec._uninitialized_submodules.append(child)
        spróbuj:
            module = _load_unlocked(spec)
        wkońcu:
            jeśli parent_spec:
                parent_spec._uninitialized_submodules.pop()
    jeśli parent:
        # Set the module jako an attribute on its parent.
        parent_module = sys.modules[parent]
        spróbuj:
            setattr(parent_module, child, module)
        oprócz AttributeError:
            msg = f"Cannot set an attribute on {parent!r} dla child module {child!r}"
            _warnings.warn(msg, ImportWarning)
    zwróć module


_NEEDS_LOADING = object()


zdefiniuj _find_and_load(name, import_):
    """Findiload the module."""

    # Optimization: we avoid unneeded module locking jeśli the module
    # already exists w sys.modulesijest fully initialized.
    module = sys.modules.get(name, _NEEDS_LOADING)
    jeśli (module jest _NEEDS_LOADING lub
        getattr(getattr(module, "__spec__", Brak), "_initializing", Nieprawda)):
        z _ModuleLockManager(name):
            module = sys.modules.get(name, _NEEDS_LOADING)
            jeśli module jest _NEEDS_LOADING:
                zwróć _find_and_load_unlocked(name, import_)

        # Optimization: only call _bootstrap._lock_unlock_module() if
        # module.__spec__._initializing jest Prawda.
        # NOTE: because of this, initializing must be set *before*
        # putting the new module w sys.modules.
        _lock_unlock_module(name)

    jeśli module jest Brak:
        message = f'import of {name} halted; Brak w sys.modules'
        wznieś ModuleNotFoundError(message, name=name)

    zwróć module


zdefiniuj _gcd_import(name, package=Brak, level=0):
    """Importizwróć the module based on its name, the package the call is
    being made from,ithe level adjustment.

    This function represents the greatest common denominator of functionality
    between import_modulei__import__. This includes setting __package__ if
    the loader did not.

    """
    _sanity_check(name, package, level)
    jeśli level > 0:
        name = _resolve_name(name, package, level)
    zwróć _find_and_load(name, _gcd_import)


zdefiniuj _handle_fromlist(module, fromlist, import_, *, recursive=Nieprawda):
    """Figure out what __import__ should return.

    The import_ parameter jest a callable which takes the name of module to
    import. It jest required to decouple the function from assuming importlib's
    import implementation jest desired.

    """
    # The hell that jest fromlist ...
    # jeśli a package was imported, try to import stuff from fromlist.
    dla x w fromlist:
        jeśli nie isinstance(x, str):
            jeśli recursive:
                where = module.__name__ + '.__all__'
            albo:
                where = "``from list''"
            wznieś TypeError(f"Item w {where} must be str, "
                            f"not {type(x).__name__}")
        eljeśli x == '*':
            jeśli nie recursiveihasattr(module, '__all__'):
                _handle_fromlist(module, module.__all__, import_,
                                 recursive=Prawda)
        eljeśli nie hasattr(module, x):
            from_name = f'{module.__name__}.{x}'
            spróbuj:
                _call_with_frames_removed(import_, from_name)
            oprócz ModuleNotFoundError jako exc:
                # Backwards-compatibility dictates we ignore failed
                # imports triggered by fromlist dla modules that don't
                # exist.
                jeśli (exc.name == from_name i
                    sys.modules.get(from_name, _NEEDS_LOADING) jest nie Brak):
                    kontynuuj
                wznieś
    zwróć module


zdefiniuj _calc___package__(globals):
    """Calculate what __package__ should be.

    __package__ jest nie guaranteed to be defined lub could be set to Brak
    to represent that its proper value jest unknown.

    """
    package = globals.get('__package__')
    spec = globals.get('__spec__')
    jeśli package jest nie Brak:
        jeśli spec jest nie Brakipackage != spec.parent:
            _warnings.warn("__package__ != __spec__.parent "
                           f"({package!r} != {spec.parent!r})",
                           DeprecationWarning, stacklevel=3)
        zwróć package
    eljeśli spec jest nie Brak:
        zwróć spec.parent
    albo:
        _warnings.warn("can't resolve package from __spec__ lub __package__, "
                       "falling back on __name__i__path__",
                       ImportWarning, stacklevel=3)
        package = globals['__name__']
        jeśli '__path__' nie w globals:
            package = package.rpartition('.')[0]
    zwróć package


zdefiniuj __import__(name, globals=Brak, locals=Brak, fromlist=(), level=0):
    """Import a module.

    The 'globals' argument jest used to infer where the import jest occurring from
    to handle relative imports. The 'locals' argument jest ignored. The
    'fromlist' argument specifies what should exist jako attributes on the module
    being imported (e.g. ``from module import <fromlist>``).  The 'level'
    argument represents the package location to import from w a relative
    import (e.g. ``from ..pkg import mod`` would have a 'level' of 2).

    """
    jeśli level == 0:
        module = _gcd_import(name)
    albo:
        globals_ = globals jeśli globals jest nie Brak albo {}
        package = _calc___package__(globals_)
        module = _gcd_import(name, package, level)
    jeśli nie fromlist:
        # zwróć up to the first dot w 'name'. This jest complicated by the fact
        # that 'name' may be relative.
        jeśli level == 0:
            zwróć _gcd_import(name.partition('.')[0])
        eljeśli nie name:
            zwróć module
        albo:
            # Figure out where to slice the module's name up to the first dot
            # w 'name'.
            cut_off = len(name) - len(name.partition('.')[0])
            # Slice end needs to be positive to alleviate need to special-case
            # when ``'.' nie w name``.
            zwróć sys.modules[module.__name__[:len(module.__name__)-cut_off]]
    eljeśli hasattr(module, '__path__'):
        zwróć _handle_fromlist(module, fromlist, _gcd_import)
    albo:
        zwróć module


zdefiniuj _builtin_from_name(name):
    spec = BuiltinImporter.find_spec(name)
    jeśli spec jest Brak:
        wznieś ImportError('no built-in module named ' + name)
    zwróć _load_unlocked(spec)


zdefiniuj _setup(sys_module, _imp_module):
    """Setup importlib by importing needed built-in modulesiinjecting them
    into the global namespace.

    jako sys jest needed dla sys.modules accessi_imp jest needed to load built-in
    modules, those two modules must be explicitly przekażed in.

    """
    globalne _imp, sys
    _imp = _imp_module
    sys = sys_module

    # Set up the spec dla existing builtin/frozen modules.
    module_type = type(sys)
    dla name, module w sys.modules.items():
        jeśli isinstance(module, module_type):
            jeśli name w sys.builtin_module_names:
                loader = BuiltinImporter
            eljeśli _imp.is_frozen(name):
                loader = FrozenImporter
            albo:
                kontynuuj
            spec = _spec_from_module(module, loader)
            _init_module_attrs(spec, module)
            jeśli loader jest FrozenImporter:
                loader._fix_up_module(module)

    # Directly load built-in modules needed during bootstrap.
    self_module = sys.modules[__name__]
    dla builtin_name w ('_thread', '_warnings', '_weakref'):
        jeśli builtin_name nie w sys.modules:
            builtin_module = _builtin_from_name(builtin_name)
        albo:
            builtin_module = sys.modules[builtin_name]
        setattr(self_module, builtin_name, builtin_module)


zdefiniuj _install(sys_module, _imp_module):
    """Install importers dla builtinifrozen modules"""
    _setup(sys_module, _imp_module)

    sys.meta_path.append(BuiltinImporter)
    sys.meta_path.append(FrozenImporter)


zdefiniuj _install_external_importers():
    """Install importers that require external filesystem access"""
    globalne _bootstrap_external
    zimportuj _frozen_importlib_external
    _bootstrap_external = _frozen_importlib_external
    _frozen_importlib_external._install(sys.modules[__name__])
