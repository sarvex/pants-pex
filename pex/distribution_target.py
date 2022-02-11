# Copyright 2019 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import os

from pex.interpreter import PythonInterpreter
from pex.orderedset import OrderedSet
from pex.pep_508 import MarkerEnvironment
from pex.platforms import Platform
from pex.third_party.packaging import tags
from pex.third_party.pkg_resources import Requirement
from pex.typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import attr  # vendor:skip
    from typing import Any, Optional, Tuple, Iterator
else:
    from pex.third_party import attr


class DistributionTarget(object):
    """Represents the target of a python distribution."""

    class AmbiguousTargetError(ValueError):
        pass

    class ManylinuxOutOfContextError(ValueError):
        pass

    @classmethod
    def current(cls):
        # type: () -> DistributionTarget
        return cls()

    @classmethod
    def for_interpreter(cls, interpreter):
        # type: (PythonInterpreter) -> DistributionTarget
        return cls(interpreter=interpreter)

    @classmethod
    def for_platform(
        cls,
        platform,  # type: Platform
        manylinux=None,  # type: Optional[str]
    ):
        # type: (...) -> DistributionTarget
        return cls(platform=platform, manylinux=manylinux)

    def __init__(
        self,
        interpreter=None,  # type: Optional[PythonInterpreter]
        platform=None,  # type:Optional[Platform]
        manylinux=None,  # type: Optional[str]
    ):
        # type: (...) -> None
        if interpreter and platform:
            raise self.AmbiguousTargetError(
                "A {class_name} can represent an interpreter or a platform but not both at the "
                "same time. Given interpreter {interpreter} and platform {platform}.".format(
                    class_name=self.__class__.__name__, interpreter=interpreter, platform=platform
                )
            )
        if not interpreter and not platform:
            interpreter = PythonInterpreter.get()
        if manylinux and not platform:
            raise self.ManylinuxOutOfContextError(
                "A value for manylinux only makes sense for platform distribution targets. Given "
                "manylinux={!r} but no platform.".format(manylinux)
            )
        self._interpreter = interpreter
        self._platform = platform
        self._manylinux = manylinux

    @property
    def is_platform(self):
        # type: () -> bool
        """Is the distribution target a platform specification.

        N.B.: This value will always be the opposite of `is_interpreter` since a distribution target
        can only encapsulate either a platform specification or a local interpreter.
        """
        return self._platform is not None

    @property
    def is_interpreter(self):
        # type: () -> bool
        """Is the distribution target a local interpreter.

        N.B.: This value will always be the opposite of `is_platform` since a distribution target
        can only encapsulate either a platform specification or a local interpreter.
        """
        return self._interpreter is not None

    @property
    def is_foreign(self):
        # type: () -> bool
        """Does the distribution target represent a foreign platform.

        A foreign platform is one not matching the current interpreter.
        """
        if self.is_interpreter:
            return False
        return self._platform not in self.get_interpreter().supported_platforms

    def get_interpreter(self):
        # type: () -> PythonInterpreter
        return self._interpreter or PythonInterpreter.get()

    def get_python_version_str(self):
        # type: () -> Optional[str]
        if self.is_platform:
            return None
        return self.get_interpreter().identity.version_str

    def get_platform(self):
        # type: () -> Tuple[Platform, Optional[str]]
        if self._platform is not None:
            return self._platform, self._manylinux
        return self.get_interpreter().platform, None

    def get_supported_tags(self):
        # type: () -> Tuple[tags.Tag, ...]
        if self._platform is not None:
            return self._platform.supported_tags(manylinux=self._manylinux)
        return self.get_interpreter().identity.supported_tags

    def requirement_applies(
        self,
        requirement,  # type: Requirement
        extras=None,  # type: Optional[Tuple[str, ...]]
    ):
        # type: (...) -> Optional[bool]
        """Determines if the given requirement applies to this distribution target.

        :param requirement: The requirement to evaluate.
        :param extras: Optional active extras.
        :returns: `True` if the requirement definitely applies, `False` if it definitely does not
                  and `None` if it might apply but not enough information is at hand to determine
                  if it does apply.
        """
        if requirement.marker is None:
            return True

        marker_environment = (
            MarkerEnvironment.from_platform(self._platform)
            if self._platform is not None
            else self.get_interpreter().identity.env_markers
        )
        if not extras:
            # Provide an empty extra to safely evaluate the markers without matching any extra.
            extras = ("",)
        for extra in extras:
            environment = marker_environment.as_dict()
            environment["extra"] = extra
            if requirement.marker.evaluate(environment=environment):
                return True

        return False

    @property
    def id(self):
        # type: () -> str
        """A unique id for this distribution target suitable as a path name component."""
        if self.is_interpreter:
            interpreter = self.get_interpreter()
            return interpreter.binary.replace(os.sep, ".").lstrip(".")
        else:
            return str(self._platform)

    def __repr__(self):
        # type: () -> str
        if self.is_interpreter:
            return "{}(interpreter={!r})".format(self.__class__.__name__, self.get_interpreter())
        else:
            return "{}(platform={!r})".format(self.__class__.__name__, self._platform)

    def _tup(self):
        # type: () -> Tuple[Any, ...]
        return self._interpreter, self._platform

    def __eq__(self, other):
        # type: (Any) -> bool
        if type(other) is not DistributionTarget:
            return NotImplemented
        return self._tup() == cast(DistributionTarget, other)._tup()

    def __hash__(self):
        # type: () -> int
        return hash(self._tup())


@attr.s(frozen=True)
class DistributionTargets(object):
    interpreters = attr.ib(default=())  # type: Tuple[PythonInterpreter, ...]
    platforms = attr.ib(default=())  # type: Tuple[Optional[Platform], ...]
    assume_manylinux = attr.ib(default=None)  # type: Optional[str]

    @property
    def interpreter(self):
        # type: () -> Optional[PythonInterpreter]
        if not self.interpreters:
            return None
        return PythonInterpreter.latest_release_of_min_compatible_version(self.interpreters)

    def unique_targets(self):
        # type: () -> OrderedSet[DistributionTarget]

        def iter_targets():
            # type: () -> Iterator[DistributionTarget]
            if not self.interpreters and not self.platforms:
                # No specified targets, so just build for the current interpreter (on the current
                # platform).
                yield DistributionTarget.current()
                return

            for interpreter in self.interpreters:
                # Build for the specified local interpreters (on the current platform).
                yield DistributionTarget.for_interpreter(interpreter)

            for platform in self.platforms:
                if platform is None and not self.interpreters:
                    # Build for the current platform (None) only if not done already (ie: no
                    # intepreters were specified).
                    yield DistributionTarget.current()
                elif platform is not None:
                    # Build for specific platforms.
                    yield DistributionTarget.for_platform(platform, manylinux=self.assume_manylinux)

        return OrderedSet(iter_targets())
