from __future__ import annotations

import errno
import json
import os
import types
import typing as t
from collections.abc import Mapping as MappingABC
from collections.abc import MutableMapping as MutableMappingABC
from collections.abc import Sequence as SequenceABC
from dataclasses import dataclass

from werkzeug.utils import import_string

if t.TYPE_CHECKING:
    import typing_extensions as te

    from .sansio.app import App


T = t.TypeVar("T")


@dataclass(frozen=True)
class ConfigLayer:
    """A named mapping used by Config.from_layers."""

    name: str
    mapping: MappingABC[str, t.Any]
    sensitive_keys: frozenset[tuple[str | int, ...]] = frozenset()

    def __post_init__(self) -> None:
        if not self.name:
            raise TypeError("Configuration layer names must be non-empty strings.")
        if isinstance(self.sensitive_keys, (str, bytes)):
            raise TypeError("sensitive_keys must be collections of key paths.")

        normalized = []
        for path in self.sensitive_keys:
            if isinstance(path, (str, bytes)):
                raise TypeError("Sensitive key paths must be tuples or lists.")
            normalized_path = tuple(path)
            if not normalized_path or not all(
                isinstance(part, (str, int)) for part in normalized_path
            ):
                raise TypeError("Sensitive key paths must contain strings or integers.")
            normalized.append(normalized_path)

        object.__setattr__(self, "sensitive_keys", frozenset(normalized))


@dataclass(frozen=True)
class ConfigValueSource:
    """Provenance information for a configuration leaf."""

    path: tuple[str | int, ...]
    layer: str
    overridden_layers: tuple[str, ...]
    sensitive: bool = False

    def __str__(self) -> str:
        path = _format_path(self.path)
        if self.sensitive:
            return path + " from " + self.layer
        if self.overridden_layers:
            overridden = ", ".join(self.overridden_layers)
            return f"{path} from {self.layer}; overrode {overridden}"
        return f"{path} from {self.layer}"

    def __repr__(self) -> str:
        return str(self)


class ConfigLoadError(ValueError):
    """A configuration layer could not be loaded."""

    def __init__(self, message: str, path: t.Sequence[str | int], layer: str) -> None:
        super().__init__(message)
        self.path = tuple(path)
        self.layer = layer


class InvalidConfigReference(ConfigLoadError):
    """A configuration reference has invalid syntax."""


class ConfigReferenceError(ConfigLoadError):
    """Base class for configuration reference resolution errors."""


class MissingConfigReference(ConfigReferenceError):
    """A configuration reference points to a missing path."""

    def __init__(
        self,
        path: t.Sequence[str | int],
        layer: str,
        reference_path: t.Sequence[str | int],
        deleted_by: str | None = None,
    ) -> None:
        target = _format_path(reference_path)
        source = _format_path(path)
        if deleted_by is not None:
            message = (
                f"Missing configuration reference {target!r}; it was deleted by"
                f" layer {deleted_by!r}. Required by {source!r} from layer"
                f" {layer!r}."
            )
        else:
            message = (
                f"Missing configuration reference {target!r} required by"
                f" {source!r} from layer {layer!r}."
            )
        super().__init__(message, path, layer)
        self.reference_path = tuple(reference_path)
        self.deleted_by = deleted_by


class CircularConfigReference(ConfigReferenceError):
    """Configuration references form a cycle."""

    def __init__(
        self,
        cycle: t.Sequence[tuple[str | int, ...]],
        layers: t.Sequence[str],
    ) -> None:
        formatted = " -> ".join(
            f"{_format_path(path)} ({layer})"
            for path, layer in zip(cycle, layers, strict=True)
        )
        super().__init__(
            f"Circular configuration reference: {formatted}.",
            cycle[-2],
            layers[-2],
        )
        self.cycle = tuple(cycle)
        self.cycle_layers = tuple(layers)


class _DeleteConfigKey:
    __slots__ = ()

    def __repr__(self) -> str:
        return "Config.DELETE"

    def __deepcopy__(self, memo: t.Any) -> _DeleteConfigKey:
        return self


DELETE_CONFIG_KEY = _DeleteConfigKey()


@dataclass(frozen=True)
class _Reference:
    segments: tuple[str | tuple[str | int, ...], ...]
    whole: bool
    path: tuple[str | int, ...]
    layer: str

    @property
    def target_path(self) -> tuple[str | int, ...]:
        return next(part for part in self.segments if isinstance(part, tuple))

    @property
    def target_paths(self) -> t.Iterator[tuple[str | int, ...]]:
        return (part for part in self.segments if isinstance(part, tuple))


def _format_path(path: t.Sequence[str | int]) -> str:
    parts: list[str] = []
    for part in path:
        if isinstance(part, int):
            parts.append(f"[{part}]")
        elif parts:
            parts.append(f".{part}")
        else:
            parts.append(str(part))
    return "".join(parts)


def _path_within(path: t.Sequence[str | int], prefix: t.Sequence[str | int]) -> bool:
    return tuple(path[: len(prefix)]) == tuple(prefix)


def _parse_reference_string(
    value: str, path: tuple[str | int, ...], layer: str
) -> str | _Reference:
    segments: list[str | tuple[str | int, ...]] = []
    literal: list[str] = []
    index = 0
    saw_reference = False

    while index < len(value):
        if value[index] != "$":
            next_index = value.find("$", index)
            if next_index == -1:
                literal.append(value[index:])
                break
            literal.append(value[index:next_index])
            index = next_index
            continue

        if index + 1 >= len(value):
            literal.append("$")
            index += 1
        elif value[index + 1] == "$":
            literal.append("$")
            index += 2
        elif value[index + 1] == "{":
            end = value.find("}", index + 2)
            reference = value[index + 2 : end] if end != -1 else ""
            if end == -1 or not reference or any(
                not part or "{" in part or "}" in part
                for part in reference.split(".")
            ):
                raise InvalidConfigReference(
                    f"Invalid configuration reference in {_format_path(path)!r}"
                    f" from layer {layer!r}.",
                    path,
                    layer,
                )
            text = "".join(literal)
            literal.clear()
            if saw_reference or text:
                segments.append(text)
            segments.append(tuple(reference.split(".")))
            saw_reference = True
            index = end + 1
        else:
            literal.append("$")
            index += 1

    trailing_text = "".join(literal)
    if trailing_text or not saw_reference:
        segments.append(trailing_text)

    references = [segment for segment in segments if isinstance(segment, tuple)]
    if not references:
        return "".join(segment for segment in segments if isinstance(segment, str))

    whole = len(segments) == 1
    return _Reference(tuple(segments), whole, path, layer)


def _copy_config_value(
    value: t.Any, path: tuple[str | int, ...], layer: str
) -> t.Any:
    if value is DELETE_CONFIG_KEY:
        return value
    if isinstance(value, str):
        return _parse_reference_string(value, path, layer)
    if isinstance(value, MappingABC):
        result: dict[str, t.Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError("Configuration mapping keys must be strings.")
            result[key] = _copy_config_value(child, (*path, key), layer)
        return result
    if isinstance(value, list):
        return [
            _copy_config_value(child, (*path, item), layer)
            for item, child in enumerate(value)
        ]
    if isinstance(value, tuple):
        return tuple(
            _copy_config_value(child, (*path, item), layer)
            for item, child in enumerate(value)
        )
    return value


class ConfigAttribute(t.Generic[T]):
    """Makes an attribute forward to the config"""

    def __init__(
        self, name: str, get_converter: t.Callable[[t.Any], T] | None = None
    ) -> None:
        self.__name__ = name
        self.get_converter = get_converter

    @t.overload
    def __get__(self, obj: None, owner: None) -> te.Self: ...

    @t.overload
    def __get__(self, obj: App, owner: type[App]) -> T: ...

    def __get__(self, obj: App | None, owner: type[App] | None = None) -> T | te.Self:
        if obj is None:
            return self

        rv = obj.config[self.__name__]

        if self.get_converter is not None:
            rv = self.get_converter(rv)

        return rv  # type: ignore[no-any-return]

    def __set__(self, obj: App, value: t.Any) -> None:
        obj.config[self.__name__] = value


class Config(dict):  # type: ignore[type-arg]
    """Works exactly like a dict but provides ways to fill it from files
    or special dictionaries.  There are two common patterns to populate the
    config.

    Either you can fill the config from a config file::

        app.config.from_pyfile('yourconfig.cfg')

    Or alternatively you can define the configuration options in the
    module that calls :meth:`from_object` or provide an import path to
    a module that should be loaded.  It is also possible to tell it to
    use the same module and with that provide the configuration values
    just before the call::

        DEBUG = True
        SECRET_KEY = 'development key'
        app.config.from_object(__name__)

    In both cases (loading from any Python file or loading from modules),
    only uppercase keys are added to the config.  This makes it possible to use
    lowercase values in the config file for temporary values that are not added
    to the config or to define the config keys in the same file that implements
    the application.

    Probably the most interesting way to load configurations is from an
    environment variable pointing to a file::

        app.config.from_envvar('YOURAPPLICATION_SETTINGS')

    In this case before launching the application you have to set this
    environment variable to the file you want to use.  On Linux and OS X
    use the export statement::

        export YOURAPPLICATION_SETTINGS='/path/to/config/file'

    On windows use `set` instead.

    :param root_path: path to which files are read relative from.  When the
                      config object is created by the application, this is
                      the application's :attr:`~flask.Flask.root_path`.
    :param defaults: an optional dictionary of default values
    """

    def __init__(
        self,
        root_path: str | os.PathLike[str],
        defaults: dict[str, t.Any] | None = None,
    ) -> None:
        super().__init__(defaults or {})
        self.root_path = root_path
        self._config_sources: dict[tuple[str | int, ...], tuple[str, ...]] = {}
        self._config_sensitive: frozenset[tuple[str | int, ...]] = frozenset()
        self._config_aliases: dict[
            tuple[str | int, ...], tuple[str | int, ...]
        ] = {}

    #: Delete a key from an earlier configuration layer.
    DELETE: t.ClassVar[_DeleteConfigKey] = DELETE_CONFIG_KEY

    def from_layers(
        self,
        *layers: ConfigLayer | tuple[str, MappingABC[str, t.Any]],
        sensitive_keys: t.Collection[t.Collection[str | int]] = (),
    ) -> bool:
        """Merge named configuration mappings in the supplied order.

        Nested mappings merge recursively; lists and other values replace the
        previous value. DELETE removes a key. A reference making up the whole
        value keeps its type; references embedded in text become text.

        The existing config is unchanged unless all layers merge and all
        references resolve successfully.
        """
        prepared_layers = self._prepare_layers(layers)
        candidate: dict[str, t.Any] = {}
        histories: dict[tuple[str | int, ...], tuple[str, ...]] = {}
        deleted_by: dict[tuple[str | int, ...], str] = {}
        sensitive = self._normalize_sensitive_paths(sensitive_keys)

        def clear_prefix(path: tuple[str | int, ...]) -> None:
            for stored_path in list(histories):
                if stored_path == path or _path_within(stored_path, path):
                    histories.pop(stored_path, None)
            for stored_path in list(deleted_by):
                if stored_path == path or _path_within(stored_path, path):
                    deleted_by.pop(stored_path, None)

        def record(
            value: t.Any,
            path: tuple[str | int, ...],
            layer_name: str,
            include_root: bool = True,
        ) -> None:
            if include_root:
                histories[path] = (*histories.get(path, ()), layer_name)
            if isinstance(value, MappingABC):
                for key, child in value.items():
                    record(child, (*path, key), layer_name)
            elif isinstance(value, (list, tuple)):
                for index, child in enumerate(value):
                    record(child, (*path, index), layer_name)

        def merge(
            target: MutableMappingABC[str, t.Any],
            mapping: MappingABC[str, t.Any],
            path: tuple[str | int, ...],
            layer_name: str,
        ) -> None:
            for key, incoming in mapping.items():
                child_path = (*path, key)

                if incoming is DELETE_CONFIG_KEY:
                    target.pop(key, None)
                    clear_prefix(child_path)
                    deleted_by[child_path] = layer_name
                    continue

                existing = target.get(key)
                if key in target and isinstance(existing, MappingABC) and isinstance(
                    incoming, MappingABC
                ):
                    existing_mapping = t.cast(MutableMappingABC[str, t.Any], existing)
                    histories[child_path] = (
                        *histories.get(child_path, ()),
                        layer_name,
                    )
                    merge(existing_mapping, incoming, child_path, layer_name)
                    continue

                old_history = histories.get(child_path, ())
                clear_prefix(child_path)
                target[key] = incoming
                if isinstance(incoming, MappingABC):
                    histories[child_path] = (layer_name,)
                else:
                    histories[child_path] = (*old_history, layer_name)
                record(incoming, child_path, layer_name, include_root=False)

        for layer in prepared_layers:
            sensitive.update(layer.sensitive_keys)
            copied = _copy_config_value(layer.mapping, (), layer.name)
            if not isinstance(copied, MappingABC):
                raise TypeError("Configuration layers must contain a mapping.")
            merge(candidate, copied, (), layer.name)

        references: list[_Reference] = []

        def collect_references(value: t.Any) -> None:
            if isinstance(value, _Reference):
                references.append(value)
            elif isinstance(value, MappingABC):
                for child in value.values():
                    collect_references(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    collect_references(child)

        collect_references(candidate)

        def deletion_layer(path: tuple[str | int, ...]) -> str | None:
            candidates: list[tuple[tuple[str | int, ...], str]] = [
                (stored, layer_name)
                for stored, layer_name in deleted_by.items()
                if path == stored or _path_within(path, stored)
            ]
            candidates.extend(
                (stored, layer_names[-1])
                for stored, layer_names in histories.items()
                if _path_within(path, stored)
            )
            if not candidates:
                return None
            return max(candidates, key=lambda item: len(item[0]))[1]

        def follow_reference(
            reference: _Reference,
            active: tuple[_Reference, ...],
        ) -> tuple[t.Any, tuple[str | int, ...]]:
            if reference.path in {active_ref.path for active_ref in active}:
                index = next(
                    item
                    for item, active_ref in enumerate(active)
                    if active_ref.path == reference.path
                )
                cycle = (*active[index:], reference)
                raise CircularConfigReference(
                    tuple(item.path for item in cycle),
                    tuple(item.layer for item in cycle),
                )

            return find(reference.target_path, (*active, reference))

        def find(
            target_path: tuple[str | int, ...],
            active: tuple[_Reference, ...],
        ) -> tuple[t.Any, tuple[str | int, ...]]:
            current: t.Any = candidate
            material_path = target_path

            for position, segment in enumerate(target_path):
                while isinstance(current, _Reference):
                    if current.whole:
                        material_path = current.target_path + target_path[position:]
                        current, _ = follow_reference(current, active)
                    else:
                        return None, target_path

                if isinstance(current, MappingABC):
                    if segment not in current:
                        return None, target_path
                    current = current[segment]
                elif isinstance(current, (list, tuple)):
                    index = segment
                    if isinstance(index, str):
                        try:
                            index = int(index)
                        except ValueError:
                            return None, target_path
                    if not isinstance(index, int) or index < 0:
                        return None, target_path
                    try:
                        current = current[index]
                    except IndexError:
                        return None, target_path
                else:
                    return None, target_path

            while isinstance(current, _Reference):
                if current.whole:
                    material_path = current.target_path
                    current, _ = follow_reference(current, active)
                else:
                    break

            return current, material_path

        def resolve(
            value: t.Any,
            path: tuple[str | int, ...],
            active: tuple[_Reference, ...] = (),
        ) -> t.Any:
            if isinstance(value, _Reference):
                if value.path in {active_ref.path for active_ref in active}:
                    index = next(
                        item
                        for item, active_ref in enumerate(active)
                        if active_ref.path == value.path
                    )
                    cycle = (*active[index:], value)
                    raise CircularConfigReference(
                        tuple(item.path for item in cycle),
                        tuple(item.layer for item in cycle),
                    )

                next_active = (*active, value)
                resolved_targets: list[t.Any] = []
                for target_path in value.target_paths:
                    raw_target, material_path = find(target_path, next_active)
                    if raw_target is None:
                        raise MissingConfigReference(
                            value.path,
                            value.layer,
                            target_path,
                            deletion_layer(target_path),
                        )
                    resolved_targets.append(
                        resolve(raw_target, material_path, next_active)
                    )

                if value.whole:
                    return resolved_targets[0]

                result: list[str] = []
                target_index = 0
                for part in value.segments:
                    if isinstance(part, tuple):
                        result.append(str(resolved_targets[target_index]))
                        target_index += 1
                    else:
                        result.append(part)
                return "".join(result)

            if isinstance(value, MappingABC):
                return {
                    key: resolve(child, (*path, key), active)
                    for key, child in value.items()
                }
            if isinstance(value, list):
                return [
                    resolve(child, (*path, index), active)
                    for index, child in enumerate(value)
                ]
            if isinstance(value, tuple):
                return tuple(
                    resolve(child, (*path, index), active)
                    for index, child in enumerate(value)
                )
            return value

        final = {key: resolve(value, (key,)) for key, value in candidate.items()}

        aliases = {
            reference.path: reference.target_path
            for reference in references
            if reference.whole
        }

        def marked_sensitive(path: tuple[str | int, ...]) -> bool:
            return any(
                path == marked or _path_within(path, marked)
                for marked in sensitive
            )

        changed = True
        while changed:
            changed = False
            for reference in references:
                if marked_sensitive(reference.path):
                    continue
                target_path = reference.target_path
                while target_path in aliases:
                    target_path = aliases[target_path]
                if any(
                    marked == target_path
                    or _path_within(target_path, marked)
                    or _path_within(marked, target_path)
                    for marked in sensitive
                ):
                    sensitive.add(reference.path)
                    changed = True

        self.clear()
        dict.update(self, final)
        self._config_sources = histories
        self._config_sensitive = frozenset(sensitive)
        self._config_aliases = aliases
        return True

    @staticmethod
    def _prepare_layers(
        layers: t.Sequence[ConfigLayer | tuple[str, MappingABC[str, t.Any]]],
    ) -> tuple[ConfigLayer, ...]:
        prepared: list[ConfigLayer] = []
        names: set[str] = set()

        for item in layers:
            if isinstance(item, ConfigLayer):
                layer = item
            elif (
                isinstance(item, SequenceABC)
                and not isinstance(item, (str, bytes))
                and len(item) == 2
            ):
                name, mapping = item
                if not isinstance(name, str) or not name:
                    raise TypeError(
                        "Configuration layer names must be non-empty strings."
                    )
                if not isinstance(mapping, MappingABC):
                    raise TypeError("Configuration layers must contain a mapping.")
                layer = ConfigLayer(name, mapping)
            else:
                raise TypeError(
                    "Configuration layers must be ConfigLayer objects or"
                    " (name, mapping) tuples."
                )

            if not layer.name:
                raise TypeError("Configuration layer names must be non-empty strings.")
            if layer.name in names:
                raise ValueError(
                    "Duplicate configuration layer name " + repr(layer.name) + "."
                )
            if not isinstance(layer.mapping, MappingABC):
                raise TypeError("Configuration layers must contain a mapping.")
            prepared.append(layer)
            names.add(layer.name)

        return tuple(prepared)

    @staticmethod
    def _normalize_sensitive_paths(
        sensitive_keys: t.Collection[t.Collection[str | int]],
    ) -> set[tuple[str | int, ...]]:
        if isinstance(sensitive_keys, (str, bytes)):
            raise TypeError("sensitive_keys must be a collection of key paths.")

        normalized: set[tuple[str | int, ...]] = set()
        for path in sensitive_keys:
            if isinstance(path, (str, bytes)):
                raise TypeError("Sensitive key paths must be tuples or lists.")
            normalized_path = tuple(path)
            if not normalized_path or not all(
                isinstance(part, (str, int)) for part in normalized_path
            ):
                raise TypeError("Sensitive key paths must contain strings or integers.")
            normalized.add(normalized_path)
        return normalized

    def get_value_source(
        self, *path: str | int | t.Collection[str | int]
    ) -> ConfigValueSource:
        """Return provenance for a leaf in a config loaded by from_layers."""
        if len(path) == 1 and isinstance(path[0], SequenceABC) and not isinstance(
            path[0], (str, bytes)
        ):
            original_path = tuple(path[0])
        elif len(path) == 1 and isinstance(path[0], str):
            original_path = tuple(path[0].split("."))
        else:
            original_path = t.cast(tuple[str | int, ...], path)

        if not original_path or not all(
            isinstance(part, (str, int)) for part in original_path
        ):
            raise KeyError("A non-empty configuration path is required.")
        if not self._config_sources:
            raise RuntimeError("Configuration was not loaded with from_layers.")

        material_path = original_path
        alias_path: tuple[str | int, ...] | None = None
        seen: set[tuple[str | int, ...]] = set()

        while True:
            for length in range(1, len(material_path) + 1):
                prefix = material_path[:length]
                if prefix in self._config_aliases:
                    if prefix in seen:
                        raise KeyError(_format_path(original_path))
                    seen.add(prefix)
                    if alias_path is None:
                        alias_path = prefix
                    material_path = (
                        self._config_aliases[prefix] + material_path[length:]
                    )
                    break
            else:
                break

        value: t.Any = self
        normalized_material_path: list[str | int] = []
        try:
            for part in material_path:
                if isinstance(value, (list, tuple)) and isinstance(part, str):
                    part = int(part)
                value = value[part]
                normalized_material_path.append(part)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise KeyError(_format_path(original_path)) from error

        if isinstance(value, MappingABC):
            raise KeyError(repr(_format_path(original_path)) + " is not a leaf value.")

        layers = self._config_sources[tuple(normalized_material_path)]
        sensitive = any(
            original_path == marked or _path_within(original_path, marked)
            for marked in self._config_sensitive
        )
        return ConfigValueSource(
            original_path,
            layers[-1],
            layers[:-1],
            sensitive,
        )

    def get_source(
        self, *path: str | int | t.Collection[str | int]
    ) -> ConfigValueSource:
        """Alias for get_value_source."""
        return self.get_value_source(*path)

    def from_envvar(self, variable_name: str, silent: bool = False) -> bool:
        """Loads a configuration from an environment variable pointing to
        a configuration file.  This is basically just a shortcut with nicer
        error messages for this line of code::

            app.config.from_pyfile(os.environ['YOURAPPLICATION_SETTINGS'])

        :param variable_name: name of the environment variable
        :param silent: set to ``True`` if you want silent failure for missing
                       files.
        :return: ``True`` if the file was loaded successfully.
        """
        rv = os.environ.get(variable_name)
        if not rv:
            if silent:
                return False
            raise RuntimeError(
                f"The environment variable {variable_name!r} is not set"
                " and as such configuration could not be loaded. Set"
                " this variable and make it point to a configuration"
                " file"
            )
        return self.from_pyfile(rv, silent=silent)

    def from_prefixed_env(
        self, prefix: str = "FLASK", *, loads: t.Callable[[str], t.Any] = json.loads
    ) -> bool:
        """Load any environment variables that start with ``FLASK_``,
        dropping the prefix from the env key for the config key. Values
        are passed through a loading function to attempt to convert them
        to more specific types than strings.

        Keys are loaded in :func:`sorted` order.

        The default loading function attempts to parse values as any
        valid JSON type, including dicts and lists.

        Specific items in nested dicts can be set by separating the
        keys with double underscores (``__``). If an intermediate key
        doesn't exist, it will be initialized to an empty dict.

        :param prefix: Load env vars that start with this prefix,
            separated with an underscore (``_``).
        :param loads: Pass each string value to this function and use
            the returned value as the config value. If any error is
            raised it is ignored and the value remains a string. The
            default is :func:`json.loads`.

        .. versionadded:: 2.1
        """
        prefix = f"{prefix}_"

        for key in sorted(os.environ):
            if not key.startswith(prefix):
                continue

            value = os.environ[key]
            key = key.removeprefix(prefix)

            try:
                value = loads(value)
            except Exception:
                # Keep the value as a string if loading failed.
                pass

            if "__" not in key:
                # A non-nested key, set directly.
                self[key] = value
                continue

            # Traverse nested dictionaries with keys separated by "__".
            current = self
            *parts, tail = key.split("__")

            for part in parts:
                # If an intermediate dict does not exist, create it.
                if part not in current:
                    current[part] = {}

                current = current[part]

            current[tail] = value

        return True

    def from_pyfile(
        self, filename: str | os.PathLike[str], silent: bool = False
    ) -> bool:
        """Updates the values in the config from a Python file.  This function
        behaves as if the file was imported as module with the
        :meth:`from_object` function.

        :param filename: the filename of the config.  This can either be an
                         absolute filename or a filename relative to the
                         root path.
        :param silent: set to ``True`` if you want silent failure for missing
                       files.
        :return: ``True`` if the file was loaded successfully.

        .. versionadded:: 0.7
           `silent` parameter.
        """
        filename = os.path.join(self.root_path, filename)
        d = types.ModuleType("config")
        d.__file__ = filename
        try:
            with open(filename, mode="rb") as config_file:
                exec(compile(config_file.read(), filename, "exec"), d.__dict__)
        except OSError as e:
            if silent and e.errno in (errno.ENOENT, errno.EISDIR, errno.ENOTDIR):
                return False
            e.strerror = f"Unable to load configuration file ({e.strerror})"
            raise
        self.from_object(d)
        return True

    def from_object(self, obj: object | str) -> None:
        """Updates the values from the given object.  An object can be of one
        of the following two types:

        -   a string: in this case the object with that name will be imported
        -   an actual object reference: that object is used directly

        Objects are usually either modules or classes. :meth:`from_object`
        loads only the uppercase attributes of the module/class. A ``dict``
        object will not work with :meth:`from_object` because the keys of a
        ``dict`` are not attributes of the ``dict`` class.

        Example of module-based configuration::

            app.config.from_object('yourapplication.default_config')
            from yourapplication import default_config
            app.config.from_object(default_config)

        Nothing is done to the object before loading. If the object is a
        class and has ``@property`` attributes, it needs to be
        instantiated before being passed to this method.

        You should not use this function to load the actual configuration but
        rather configuration defaults.  The actual config should be loaded
        with :meth:`from_pyfile` and ideally from a location not within the
        package because the package might be installed system wide.

        See :ref:`config-dev-prod` for an example of class-based configuration
        using :meth:`from_object`.

        :param obj: an import name or object
        """
        if isinstance(obj, str):
            obj = import_string(obj)
        for key in dir(obj):
            if key.isupper():
                self[key] = getattr(obj, key)

    def from_file(
        self,
        filename: str | os.PathLike[str],
        load: t.Callable[[t.IO[t.Any]], t.Mapping[str, t.Any]],
        silent: bool = False,
        text: bool = True,
    ) -> bool:
        """Update the values in the config from a file that is loaded
        using the ``load`` parameter. The loaded data is passed to the
        :meth:`from_mapping` method.

        .. code-block:: python

            import json
            app.config.from_file("config.json", load=json.load)

            import tomllib
            app.config.from_file("config.toml", load=tomllib.load, text=False)

        :param filename: The path to the data file. This can be an
            absolute path or relative to the config root path.
        :param load: A callable that takes a file handle and returns a
            mapping of loaded data from the file.
        :type load: ``Callable[[Reader], Mapping]`` where ``Reader``
            implements a ``read`` method.
        :param silent: Ignore the file if it doesn't exist.
        :param text: Open the file in text or binary mode.
        :return: ``True`` if the file was loaded successfully.

        .. versionchanged:: 2.3
            The ``text`` parameter was added.

        .. versionadded:: 2.0
        """
        filename = os.path.join(self.root_path, filename)

        try:
            with open(filename, "r" if text else "rb") as f:
                obj = load(f)
        except OSError as e:
            if silent and e.errno in (errno.ENOENT, errno.EISDIR):
                return False

            e.strerror = f"Unable to load configuration file ({e.strerror})"
            raise

        return self.from_mapping(obj)

    def from_mapping(
        self, mapping: t.Mapping[str, t.Any] | None = None, **kwargs: t.Any
    ) -> bool:
        """Updates the config like :meth:`update` ignoring items with
        non-upper keys.

        :return: Always returns ``True``.

        .. versionadded:: 0.11
        """
        mappings: dict[str, t.Any] = {}
        if mapping is not None:
            mappings.update(mapping)
        mappings.update(kwargs)
        for key, value in mappings.items():
            if key.isupper():
                self[key] = value
        return True

    def get_namespace(
        self, namespace: str, lowercase: bool = True, trim_namespace: bool = True
    ) -> dict[str, t.Any]:
        """Returns a dictionary containing a subset of configuration options
        that match the specified namespace/prefix. Example usage::

            app.config['IMAGE_STORE_TYPE'] = 'fs'
            app.config['IMAGE_STORE_PATH'] = '/var/app/images'
            app.config['IMAGE_STORE_BASE_URL'] = 'http://img.website.com'
            image_store_config = app.config.get_namespace('IMAGE_STORE_')

        The resulting dictionary `image_store_config` would look like::

            {
                'type': 'fs',
                'path': '/var/app/images',
                'base_url': 'http://img.website.com'
            }

        This is often useful when configuration options map directly to
        keyword arguments in functions or class constructors.

        :param namespace: a configuration namespace
        :param lowercase: a flag indicating if the keys of the resulting
                          dictionary should be lowercase
        :param trim_namespace: a flag indicating if the keys of the resulting
                          dictionary should not include the namespace

        .. versionadded:: 0.11
        """
        rv = {}
        for k, v in self.items():
            if not k.startswith(namespace):
                continue
            if trim_namespace:
                key = k[len(namespace) :]
            else:
                key = k
            if lowercase:
                key = key.lower()
            rv[key] = v
        return rv

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {dict.__repr__(self)}>"
