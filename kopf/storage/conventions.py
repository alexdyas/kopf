"""
Some reusable implementation details regarding naming in K8s.

This module implements conventions for annotations & labels with restrictions.
They are used to identify operator's own keys consistently during the run,
keep backward- & forward-compatibility of naming schemas across the versions.

For some fields, such as annotations and labels, K8s puts extra restrictions
on the alphabet used and on lengths of the names, name parts, and values.
All such restrictions are implemented here in combination with the conventions.

Terminology (to be on the same page; `aligned with K8s's documentation`__):

__ https://kubernetes.io/docs/concepts/overview/working-with-objects/annotations/#syntax-and-character-set

* **prefix** is a fqdn-like optional part (e.g. `kopf.zalando.org/`);
* **name** is the main part of an annotation/label (e.g. `kopf-managed`);
* **key** (a dictionary key, a full key) is the **prefix** plus the **name**,
  possibly suffixed, infixed (named-prefixed), fully or partially hashed
  (e.g. `example.com/kopf-managed` or `kopf.zalando.org/handler1.subhandlerA`),
  but used as a dictionary key for the annotations (hence the name).

Note: there are also progress storages' **record keys**, which are not related
to the annotations/labels keys of this convention: they correspond to the names:
in most cases, they will be used as the annotation names with special symbols
replaced; in some cases, they will be cut and hash-suffixed.
"""
import base64
import hashlib
import warnings
from typing import Any, Iterable, Optional


class StorageKeyFormingConvention:
    """
    A helper mixin to manage annotations/labels naming as per K8s restrictions.

    Used both in the diff-base storages and the progress storages where
    applicable. It provides a few optional methods to manage annotation
    prefixes, keys, and names (in this context, a name is a prefix + a key).
    Specifically, the annotations keys are split to V1 & V2 (would be V3, etc).

    **V1** keys were implemented overly restrictive: the length of 63 chars
    was applied to the whole annotation key, including the prefix.

    This caused unnecessary and avoidable loss of useful information: e.g.
    ``lengthy-operator-name-to-hit-63-chars.example.com/update-OJOYLA``
    instead of
    ``lengthy-operator-name-to-hit-63-chars.example.com/update.sub1``.

    `K8s says`__ that only the name is max 63 chars long, while the whole key
    (i.e. including the prefix, if present) can be max 253 chars.

    __ https://kubernetes.io/docs/concepts/overview/working-with-objects/annotations/#syntax-and-character-set

    **V2** keys implement this new hashing approach, trying to keep as much
    information in the keys as possible. Only the really lengthy keys
    will be cut the same way as V1 keys.

    If the prefix is longer than 189 chars (253-63-1), the full key could
    be longer than the limit of 253 chars -- e.g. with lengthy handler ids,
    more often for field-handlers or sub-handlers. In that case,
    the annotation keys are not shortened, and the patching would fail.

    It is the developer's responsibility to choose the prefix short enough
    to fit into K8s's restrictions. However, a warning is issued on storage
    creation, so that the strict-mode operators could convert the warning
    into an exception, thus failing the operator startup.

    For smoother upgrades of operators from V1 to V2, and for safer rollbacks
    from V2 to V1, both versions of keys are stored in annotations.
    At some point in time, the V1 keys will be read, purged, but not stored,
    thus cutting the rollback possibilities to Kopf versions with V1 keys.

    Since the annotations are purged in case of a successful handling cycle,
    this multi-versioned behaviour will most likely be unnoticed by the users,
    except when investigating the issues with persistence.

    This mode can be controlled via the storage's constructor parameter
    ``v1=True/False`` (the default is ``True`` for the time of transition).
    """

    def __init__(
            self,
            *args: Any,
            prefix: Optional[str],
            v1: bool,
            **kwargs: Any,
    ) -> None:
        # TODO: Remove type-ignore when this is fixed: https://github.com/python/mypy/issues/5887
        #       Too many arguments for "__init__" of "object" -- but this is typical for mixins!
        super().__init__(*args, **kwargs)  # type: ignore
        self.prefix = prefix
        self.v1 = v1

        # 253 is the max length, 63 is the most lengthy name part, 1 is for the "/" separator.
        if len(self.prefix or '') > 253 - 63 - 1:
            warnings.warn("The annotations prefix is too long. It can cause errors when PATCHing.")

    def make_key(self, key: str, max_length: int = 63) -> str:
        warnings.warn("make_key() is deprecated; use make_v1_key(), make_v2_key(), make_keys(), "
                      "or avoid making the keys directly at all.", DeprecationWarning)
        return self.make_v1_key(key, max_length=max_length)

    def make_keys(self, key: str) -> Iterable[str]:
        v2_keys = [self.make_v2_key(key)]
        v1_keys = [self.make_v1_key(key)] if self.v1 else []
        return v2_keys + list(set(v1_keys) - set(v2_keys))

    def make_v1_key(self, key: str, max_length: int = 63) -> str:

        # K8s has a limitation on the allowed charsets in annotation/label keys.
        # https://kubernetes.io/docs/concepts/overview/working-with-objects/annotations/#syntax-and-character-set
        safe_key = key.replace('/', '.')

        # K8s has a limitation of 63 chars per annotation/label key.
        # Force it to 63 chars by replacing the tail with a consistent hash (with full alphabet).
        # Force it to end with alnums instead of altchars or trailing chars (K8s requirement).
        prefix = f'{self.prefix}/' if self.prefix else ''
        if len(safe_key) <= max_length - len(prefix):
            suffix = ''
        else:
            suffix = self.make_suffix(safe_key)

        full_key = f'{prefix}{safe_key[:max_length - len(prefix) - len(suffix)]}{suffix}'
        return full_key

    def make_v2_key(self, key: str, max_length: int = 63) -> str:
        prefix = f'{self.prefix}/' if self.prefix else ''
        suffix = self.make_suffix(key) if len(key) > max_length else ''
        key_limit = max(0, max_length - len(suffix))
        clean_key = key.replace('/', '.')
        final_key = f'{prefix}{clean_key[:key_limit]}{suffix}'
        return final_key

    def make_suffix(self, key: str) -> str:
        digest = hashlib.blake2b(key.encode('utf-8'), digest_size=4).digest()
        alnums = base64.b64encode(digest, altchars=b'-.').decode('ascii')
        return f'-{alnums}'.rstrip('=-.')
