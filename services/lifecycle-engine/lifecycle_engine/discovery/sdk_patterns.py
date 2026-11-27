"""Catalog of verified Flagsmith JavaScript/TypeScript SDK entry points.

Only APIs listed here are treated as Flagsmith. Receivers must be traced to one of
the module specifiers below (import, require, instance creation, hook result or a
type annotation) for a reference to be CONFIRMED.

Sources: Flagsmith JS client (`flagsmith`), React bindings (`flagsmith/react`) and
the Node server SDK (`flagsmith-nodejs`) public APIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..schemas.discovery import FlagApi, FlagsmithSdk, UsageType


class BindingKind(str, Enum):
    CLIENT = "client"                    # JS client instance: hasFeature/getValue/getTrait
    CLIENT_FACTORY = "client_factory"    # createFlagsmithInstance()
    CLIENT_TYPE = "client_type"          # IFlagsmith type
    SERVER_CLASS = "server_class"        # flagsmith-nodejs Flagsmith constructor
    SERVER_CLIENT = "server_client"      # new Flagsmith(...)
    SERVER_FLAGS = "server_flags"        # result of getEnvironmentFlags/getIdentityFlags
    SERVER_FLAGS_TYPE = "server_flags_type"
    USE_FLAGS_HOOK = "use_flags_hook"
    USE_FLAGSMITH_HOOK = "use_flagsmith_hook"
    REACT_FLAGS = "react_flags"          # useFlags([...]) result object
    REACT_FLAG = "react_flag"            # destructured single flag from useFlags result


MODULE_SDK: dict[str, FlagsmithSdk] = {
    "flagsmith": FlagsmithSdk.JS_CLIENT,
    "flagsmith/isomorphic": FlagsmithSdk.JS_CLIENT,
    "flagsmith-es": FlagsmithSdk.JS_CLIENT,
    "flagsmith-es/isomorphic": FlagsmithSdk.JS_CLIENT,
    "react-native-flagsmith": FlagsmithSdk.JS_CLIENT,
    "flagsmith/react": FlagsmithSdk.REACT,
    "flagsmith-es/react": FlagsmithSdk.REACT,
    "flagsmith-nodejs": FlagsmithSdk.NODE_SERVER,
}


def module_sdk(specifier: str) -> FlagsmithSdk | None:
    return MODULE_SDK.get(specifier)


# (sdk, imported name) -> binding kind. "default" is the default export, "*" a namespace import.
EXPORTS: dict[tuple[FlagsmithSdk, str], BindingKind] = {
    (FlagsmithSdk.JS_CLIENT, "default"): BindingKind.CLIENT,
    (FlagsmithSdk.JS_CLIENT, "*"): BindingKind.CLIENT,
    (FlagsmithSdk.JS_CLIENT, "createFlagsmithInstance"): BindingKind.CLIENT_FACTORY,
    (FlagsmithSdk.JS_CLIENT, "IFlagsmith"): BindingKind.CLIENT_TYPE,
    (FlagsmithSdk.REACT, "useFlags"): BindingKind.USE_FLAGS_HOOK,
    (FlagsmithSdk.REACT, "useFlagsmith"): BindingKind.USE_FLAGSMITH_HOOK,
    (FlagsmithSdk.NODE_SERVER, "default"): BindingKind.SERVER_CLASS,
    (FlagsmithSdk.NODE_SERVER, "*"): BindingKind.SERVER_CLASS,
    (FlagsmithSdk.NODE_SERVER, "Flagsmith"): BindingKind.SERVER_CLASS,
    (FlagsmithSdk.NODE_SERVER, "Flags"): BindingKind.SERVER_FLAGS_TYPE,
}

TYPE_TO_INSTANCE = {
    BindingKind.CLIENT_TYPE: BindingKind.CLIENT,
    BindingKind.SERVER_FLAGS_TYPE: BindingKind.SERVER_FLAGS,
}


@dataclass(frozen=True)
class ApiSpec:
    api: FlagApi
    usage: UsageType
    name_arg_index: int = 0


# Methods evaluated on a receiver of the given kind.
RECEIVER_APIS: dict[BindingKind, dict[str, ApiSpec]] = {
    BindingKind.CLIENT: {
        "hasFeature": ApiSpec(FlagApi.HAS_FEATURE, UsageType.BOOLEAN_EVALUATION),
        "getValue": ApiSpec(FlagApi.GET_VALUE, UsageType.VALUE_RETRIEVAL),
        "getTrait": ApiSpec(FlagApi.GET_TRAIT, UsageType.TRAIT_READ),
    },
    BindingKind.SERVER_FLAGS: {
        "isFeatureEnabled": ApiSpec(FlagApi.IS_FEATURE_ENABLED, UsageType.BOOLEAN_EVALUATION),
        "getFeatureValue": ApiSpec(FlagApi.GET_FEATURE_VALUE, UsageType.VALUE_RETRIEVAL),
        "getFlag": ApiSpec(FlagApi.GET_FLAG, UsageType.FLAG_OBJECT_RETRIEVAL),
    },
}

# Server client methods that return a flags object.
SERVER_FLAGS_FACTORIES = {"getEnvironmentFlags", "getIdentityFlags"}

KIND_SDK = {
    BindingKind.CLIENT: FlagsmithSdk.JS_CLIENT,
    BindingKind.SERVER_FLAGS: FlagsmithSdk.NODE_SERVER,
    BindingKind.REACT_FLAGS: FlagsmithSdk.REACT,
    BindingKind.REACT_FLAG: FlagsmithSdk.REACT,
}

# Method names that *look* like Flagsmith evaluation APIs when the receiver is unknown.
AMBIGUOUS_METHODS: dict[str, ApiSpec] = {
    **RECEIVER_APIS[BindingKind.CLIENT],
    **RECEIVER_APIS[BindingKind.SERVER_FLAGS],
}

FLAG_PROPERTY_USAGE = {
    "enabled": UsageType.BOOLEAN_EVALUATION,
    "value": UsageType.VALUE_RETRIEVAL,
}
