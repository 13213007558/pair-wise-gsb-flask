from __future__ import annotations

import errno
import json
import os
import types
import typing as t
from collections.abc import Mapping
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass

from werkzeug.utils import import_string

if t.TYPE_CHECKING:
    import typing_extensions as te

    from .sansio.app import App


T = t.TypeVar("T")


class ConfigError(ValueError):
    """A layered configuration could not be loaded."""


class ConfigReferenceError(ConfigError):
    """A configuration reference was missing or circular."""

    def __init__(
        self,
        message: str,
        *,
        path: tuple[str, ...],
        source_path: tuple[tuple[str, ...], str] | None = None,
        deleted_layer: str | None = None,
        cycle: tuple[tuple[tuple[str, ...], str], ...] = (),
    ) -> None:
        super().__init__(message)
        self.path = path
        self.source_path = source_path
        self.deleted_layer = deleted_layer
        self.cycle = cycle


class _DeleteSentinel:
    __slots__ = ()

    def __repr__(self) -> str:
        return "DELETE"


DELETE = _DeleteSentinel()


def _format_path(path: tuple[str, ...]) -> str:
    return ".".join(path)


@dataclass
class _RawValue:
    value: t.Any
    layer: str
    history: list[str]
    sensitive: bool = False


@dataclass
class _DeletedValue:
    layer: str
    old: t.Any = None


class _MappingValue(dict[str, t.Any]):
    def __init__(self, layer: str, history: list[str], sensitive: bool = False) -> None:
        super().__init__()
        self.layer = layer
        self.history = history
        self.sensitive = sensitive


class _ListValue(list[t.Any]):
    def __init__(self, layer: str, history: list[str], sensitive: bool = False) -> None:
        super().__init__()
        self.layer = layer
        self.history = history
        self.sensitive = sensitive


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
        self._sources: _MappingValue | None = None
        self._sensitive_paths: set[tuple[str, ...]] = set()

    DELETE = DELETE

    def from_layers(
        self,
        *layers: tuple[str, Mapping[str, t.Any]],
        sensitive: t.Collection[str | t.Sequence[str]] = (),
    ) -> bool:
        """Merge named configuration mappings in order.

        Nested mappings are merged recursively. Lists and non-mapping values
        replace the previous value at their path. Config.DELETE removes a value
        from an earlier layer. References in strings are resolved after all
        layers have been merged.

        The current config is replaced only after the complete merged config
        has been built and resolved successfully.
        """
        prepared_layers: list[tuple[str, Mapping[str, t.Any]]] = []
        seen_names: set[str] = set()

        for layer in layers:
            if (
                not isinstance(layer, tuple)
                or len(layer) != 2
                or not isinstance(layer[0], str)
                or not isinstance(layer[1], Mapping)
            ):
                raise TypeError("Each layer must be a (name, mapping) tuple.")

            name = layer[0]
            if not name:
                raise ValueError("A configuration layer name cannot be empty.")
            if name in seen_names:
                raise ValueError(f"Duplicate configuration layer name {name!r}.")

            seen_names.add(name)
            prepared_layers.append((name, layer[1]))

        sensitive_paths = [self._normalize_sensitive_path(path) for path in sensitive]
        candidate = _MappingValue("", [])

        for name, mapping in prepared_layers:
            self._merge_mapping(candidate, mapping, name, ())

        for path in sensitive_paths:
            node = self._find_source_node(candidate, path, required=False)
            if isinstance(node, _DeletedValue) or node is None:
                raise ConfigError(
                    f"Sensitive configuration path {_format_path(path)!r} does"
                    " not exist."
                )
            self._mark_sensitive(node)

        resolver = _ReferenceResolver(candidate)
        resolved = t.cast(dict[str, t.Any], resolver.resolve(candidate, ()))

        self.clear()
        self.update(resolved)
        self._sources = candidate
        self._sensitive_paths = resolver.sensitive_paths
        return True

    @classmethod
    def _normalize_sensitive_path(cls, path: str | t.Sequence[str]) -> tuple[str, ...]:
        if isinstance(path, str):
            return tuple(path.split("."))
        if not isinstance(path, Sequence) or isinstance(path, bytes):
            raise TypeError("Sensitive paths must be strings or string sequences.")
        normalized = tuple(str(part) for part in path)
        if not normalized or any(not part for part in normalized):
            raise ValueError("Sensitive paths cannot be empty.")
        return normalized

    @classmethod
    def _normalize_path(cls, path: str | t.Sequence[str]) -> tuple[str, ...]:
        return cls._normalize_sensitive_path(path)

    def _merge_mapping(
        self,
        target: _MappingValue,
        source: Mapping[str, t.Any],
        layer: str,
        path: tuple[str, ...],
    ) -> None:
        for key, value in source.items():
            if not isinstance(key, str):
                raise ConfigError(
                    "Configuration keys must be strings at"
                    f" {_format_path((*path, repr(key)))}."
                )
            if not path and not key.isupper():
                continue

            incoming = self._make_node(value, layer)
            if isinstance(incoming, _DeletedValue):
                incoming.old = target.get(key)
            current = target.get(key)
            target[key] = self._merge_node(current, incoming, layer)

    def _make_node(self, value: t.Any, layer: str) -> t.Any:
        if value is DELETE:
            return _DeletedValue(layer)
        if isinstance(value, _DeletedValue):
            return _DeletedValue(value.layer, value.old)
        if isinstance(value, Mapping):
            mapping_node = _MappingValue(layer, [layer])
            for key, child in value.items():
                mapping_node[key] = self._make_node(child, layer)
            return mapping_node
        if isinstance(value, list):
            list_node = _ListValue(layer, [layer])
            list_node.extend(self._make_node(child, layer) for child in value)
            return list_node
        if isinstance(value, (_RawValue, _MappingValue, _ListValue)):
            return deepcopy(value)
        return _RawValue(deepcopy(value), layer, [layer])

    def _merge_node(self, current: t.Any, incoming: t.Any, layer: str) -> t.Any:
        if isinstance(incoming, _DeletedValue):
            return incoming

        if isinstance(incoming, _MappingValue):
            if isinstance(current, _MappingValue):
                if layer not in current.history:
                    current.history.append(layer)
                current.layer = layer
                for key, child in incoming.items():
                    current[key] = self._merge_node(current.get(key), child, layer)
                return current
            if isinstance(current, _DeletedValue) and isinstance(
                current.old, _MappingValue
            ):
                self._inherit_mapping_history(current.old, incoming)
            incoming.history = self._combined_history(current, incoming)
            return incoming

        if isinstance(incoming, _ListValue):
            if isinstance(current, _DeletedValue) and isinstance(
                current.old, _ListValue
            ):
                incoming.history = self._combined_history(current.old, incoming)
            else:
                incoming.history = self._combined_history(current, incoming)
            return incoming

        if isinstance(current, _DeletedValue):
            incoming.history = self._combined_history(current.old, incoming)
            return incoming

        incoming.history = self._combined_history(current, incoming)
        return incoming

    def _combined_history(self, current: t.Any, incoming: t.Any) -> list[str]:
        history = getattr(current, "history", [])
        return [*history, *(name for name in incoming.history if name not in history)]

    def _inherit_mapping_history(
        self, current: _MappingValue, incoming: _MappingValue
    ) -> None:
        for key, child in incoming.items():
            old_child = current.get(key)
            if old_child is None:
                continue
            child.history = self._combined_history(old_child, child)
            if isinstance(child, _MappingValue) and isinstance(
                old_child, _MappingValue
            ):
                self._inherit_mapping_history(old_child, child)

    def _mark_sensitive(self, node: t.Any) -> None:
        if hasattr(node, "sensitive"):
            node.sensitive = True
        if isinstance(node, _MappingValue):
            for child in node.values():
                self._mark_sensitive(child)
        elif isinstance(node, _ListValue):
            for child in node:
                self._mark_sensitive(child)

    def _find_source_node(
        self,
        root: _MappingValue | None,
        path: tuple[str, ...],
        *,
        required: bool = True,
    ) -> t.Any:
        if root is None:
            if required:
                raise KeyError(_format_path(path))
            return None

        node: t.Any = root
        missing = object()
        current_path: tuple[str, ...] = ()

        for part in path:
            current_path = (*current_path, part)
            if isinstance(node, _MappingValue):
                node = node.get(part, missing)
            elif isinstance(node, _ListValue) and part.lstrip("-").isdigit():
                index = int(part)
                try:
                    node = node[index]
                except IndexError:
                    node = missing
            else:
                node = missing

            if node is missing or isinstance(node, _DeletedValue):
                if required:
                    raise KeyError(_format_path(current_path))
                return node

        return node

    def source_for(self, path: str | t.Sequence[str]) -> str:
        """Return the layer that supplied the value at path."""
        normalized = self._normalize_path(path)
        node = self._find_source_node(self._sources, normalized)
        return t.cast(str, node.layer)

    def sources_for(self, path: str | t.Sequence[str]) -> list[str]:
        """Return all layers that supplied the path, oldest to newest."""
        normalized = self._normalize_path(path)
        node = self._find_source_node(self._sources, normalized)
        return [*t.cast(list[str], node.history)]

    def is_sensitive(self, path: str | t.Sequence[str]) -> bool:
        """Return whether a resolved path contains sensitive data."""
        return self._normalize_path(path) in self._sensitive_paths

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self._redacted(dict(self), ())!r}>"

    def _redacted(self, value: t.Any, path: tuple[str, ...]) -> t.Any:
        if path in self._sensitive_paths:
            return "<redacted>"
        if isinstance(value, dict):
            return {
                key: self._redacted(child, (*path, key)) for key, child in value.items()
            }
        if isinstance(value, list):
            return [
                self._redacted(child, (*path, str(index)))
                for index, child in enumerate(value)
            ]
        return value

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


class _ReferenceResolver:
    def __init__(self, root: _MappingValue) -> None:
        self.root = root
        self.sensitive_paths: set[tuple[str, ...]] = set()
        self.active: list[tuple[str, ...]] = []

    def resolve(self, node: t.Any, path: tuple[str, ...]) -> t.Any:
        owns_path = path not in self.active
        if owns_path:
            self.active.append(path)
        try:
            if isinstance(node, _MappingValue):
                resolved_mapping = {
                    key: self.resolve(child, (*path, key))
                    for key, child in node.items()
                    if not isinstance(child, _DeletedValue)
                }
                if node.sensitive:
                    self._mark_path(path)
                return resolved_mapping

            if isinstance(node, _ListValue):
                resolved_list = [
                    self.resolve(child, (*path, str(index)))
                    for index, child in enumerate(node)
                ]
                if node.sensitive:
                    self._mark_path(path)
                return resolved_list

            if isinstance(node, _DeletedValue):
                raise ConfigError("DELETE cannot be used inside a list.")

            if not isinstance(node, _RawValue):
                return node

            if not isinstance(node.value, str):
                if node.sensitive:
                    self._mark_path(path)
                return node.value

            parts = self._parse(node.value, path, node.layer)
            references = [part for part in parts if part[0] == "ref"]

            if not references:
                value = "".join(part[1] for part in parts)
                if node.sensitive:
                    self._mark_path(path)
                return value

            if len(parts) == 1:
                _, ref_path = parts[0]
                value, sensitive = self._resolve_reference(ref_path, path, node.layer)
                if node.sensitive or sensitive:
                    self._mark_path(path)
                return value

            rendered: list[str] = []
            sensitive = node.sensitive
            for kind, value in parts:
                if kind == "text":
                    rendered.append(value)
                    continue

                resolved_value, resolved_sensitive = self._resolve_reference(
                    t.cast(tuple[str, ...], value), path, node.layer
                )
                sensitive = sensitive or resolved_sensitive
                rendered.append(str(resolved_value))

            if sensitive:
                self._mark_path(path)
            return "".join(rendered)
        finally:
            if owns_path and self.active and self.active[-1] == path:
                self.active.pop()

    def _resolve_reference(
        self,
        ref_path: tuple[str, ...],
        source_path: tuple[str, ...],
        source_layer: str,
    ) -> tuple[t.Any, bool]:
        if ref_path in self.active:
            cycle_start = self.active.index(ref_path)
            chain_paths = (*self.active[cycle_start:], ref_path)
            cycle = tuple(
                (chain_path, self._layer_for(chain_path)) for chain_path in chain_paths
            )
            formatted_cycle = " -> ".join(
                f"{_format_path(chain_path)} (from {layer})"
                for chain_path, layer in cycle
            )
            raise ConfigReferenceError(
                f"Circular configuration reference at"
                f" {_format_path(source_path)} (from {source_layer}):"
                f" {formatted_cycle}.",
                path=source_path,
                source_path=(source_path, source_layer),
                cycle=cycle,
            )

        target = self._lookup(ref_path, source_path, source_layer)
        value = self.resolve(target, ref_path)
        sensitive = self._target_is_sensitive(target, ref_path)
        return value, sensitive

    def _lookup(
        self,
        ref_path: tuple[str, ...],
        source_path: tuple[str, ...],
        source_layer: str,
    ) -> t.Any:
        node: t.Any = self.root
        current_path: tuple[str, ...] = ()

        for part in ref_path:
            current_path = (*current_path, part)

            if isinstance(node, _MappingValue):
                if part not in node:
                    self._missing_reference(ref_path, source_path, source_layer, None)
                node = node[part]
            elif isinstance(node, _ListValue):
                if not part.lstrip("-").isdigit():
                    self._missing_reference(ref_path, source_path, source_layer, None)
                try:
                    node = node[int(part)]
                except IndexError:
                    self._missing_reference(ref_path, source_path, source_layer, None)
            else:
                self._missing_reference(ref_path, source_path, source_layer, None)

            if isinstance(node, _DeletedValue):
                self._missing_reference(ref_path, source_path, source_layer, node.layer)

        return node

    def _missing_reference(
        self,
        ref_path: tuple[str, ...],
        source_path: tuple[str, ...],
        source_layer: str,
        deleted_layer: str | None,
    ) -> t.NoReturn:
        deletion = ""
        if deleted_layer is not None:
            deletion = f" It was deleted by layer {deleted_layer!r}."
        raise ConfigReferenceError(
            f"Missing configuration reference at {_format_path(source_path)}"
            f" (from {source_layer}): {_format_path(ref_path)} does not"
            f" exist.{deletion}",
            path=source_path,
            source_path=(source_path, source_layer),
            deleted_layer=deleted_layer,
        )

    def _parse(
        self, value: str, path: tuple[str, ...], layer: str
    ) -> list[tuple[str, t.Any]]:
        parts: list[tuple[str, t.Any]] = []
        text_parts: list[str] = []
        index = 0

        def flush_text() -> None:
            if text_parts:
                parts.append(("text", "".join(text_parts)))
                text_parts.clear()

        while index < len(value):
            char = value[index]

            if char == "$" and index + 1 < len(value) and value[index + 1] == "$":
                text_parts.append("$")
                index += 2
                continue

            if (
                char == "\\"
                and index + 2 < len(value)
                and value[index + 1] == "$"
                and value[index + 2] == "{"
            ):
                text_parts.append("$" + "{")
                index += 3
                continue

            if char == "$" and index + 1 < len(value) and value[index + 1] == "{":
                end = value.find("}", index + 2)
                if end == -1:
                    raise ConfigReferenceError(
                        f"Unclosed configuration reference at"
                        f" {_format_path(path)} (from {layer}).",
                        path=path,
                        source_path=(path, layer),
                    )

                expression = value[index + 2 : end]
                ref_path = tuple(expression.split("."))
                if not expression or any(not part for part in ref_path):
                    raise ConfigReferenceError(
                        f"Invalid configuration reference at"
                        f" {_format_path(path)} (from {layer}): {expression!r}.",
                        path=path,
                        source_path=(path, layer),
                    )

                flush_text()
                parts.append(("ref", ref_path))
                index = end + 1
                continue

            text_parts.append(char)
            index += 1

        flush_text()
        return parts

    def _target_is_sensitive(self, target: t.Any, path: tuple[str, ...]) -> bool:
        if path in self.sensitive_paths:
            return True
        return bool(getattr(target, "sensitive", False))

    def _layer_for(self, path: tuple[str, ...]) -> str:
        node: t.Any = self.root
        for part in path:
            if isinstance(node, _MappingValue):
                node = node.get(part)
            elif isinstance(node, _ListValue) and part.lstrip("-").isdigit():
                node = node[int(part)]
            else:
                return "unknown"
        return t.cast(str, getattr(node, "layer", "unknown"))

    def _mark_path(self, path: tuple[str, ...]) -> None:
        for index in range(len(path) + 1):
            prefix = path[:index]
            if prefix:
                self.sensitive_paths.add(prefix)
