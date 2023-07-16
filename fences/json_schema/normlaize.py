from typing import List, Union, Set, Dict, Tuple
from fences.core.exception import NormalizationException
from fences.json_schema.json_pointer import JsonPointer
import math
import hashlib
import json

SchemaType = Union[dict, bool]

NORM_FALSE = {'anyOf': [{'type': []}]}
NORM_TRUE = {'anyOf': [{}]}


class Resolver:

    def __init__(self, schema: SchemaType):
        self.schema = schema
        self.cache = {}

    def resolve(self, pointer: JsonPointer) -> SchemaType:
        return pointer.lookup(self.schema)

    def add_normalized_schema(self, pointer: JsonPointer):
        key = str(pointer)
        assert key not in self.cache


def invert(schema: dict) -> dict:
    return schema


def _merge_type(a: Union[List[str], str], b: Union[List[str], str]) -> List[str]:
    if isinstance(a, str):
        a = [a]
    if isinstance(b, str):
        b = [b]
    return list(set(a) & set(b))


_simple_mergers = {
    'required': lambda a, b: list(set(a) | set(b)),
    'multipleOf': lambda a, b: abs(a*b) // math.gcd(a, b),
    'items': lambda a, b: {'allOf': [a, b]},
    'minimum': lambda a, b: max(a, b),
    'maximum': lambda a, b: min(a, b),
    'type': _merge_type,
    'minItems': lambda a, b: max(a, b),
    'pattern': lambda a, b: f"({a})&({b})",
    'minLength': lambda a, b: max(a, b),
    'maxLength': lambda a, b: min(a, b),
    'const': lambda a, b: a,  # todo
    'enum': lambda a, b: a + b,
    'format': lambda a, b: a, # todo
    'dependentRequired': lambda a, b: a, # todo
    'deprecated': lambda a, b: a or b,
}


def _merge_properties(result: dict, to_add: dict) -> dict:
    # Must merge properties and additionalProperties together
    # Schema 1: a: 1a,    b: 1b,              ...: 1n
    # Schema 2:           b: 2b,    c: 2c,    ...: 2n
    # Result:   a: 1a+2n, b: 1b+2b, c: 2c+1n, ...: 1n+2n

    props_result = result.get('properties', {})
    props_to_add = to_add.get('properties', {})
    additional_result = result.get('additionalProperties')
    additional_to_add = to_add.get('additionalProperties')
    for property_name, schema in props_result.items():
        if property_name in props_to_add:
            props_result[property_name] = {'allOf': [
                schema, props_to_add[property_name]
            ]}
        else:
            if additional_to_add is None:
                props_result[property_name] = schema
            else:
                props_result[property_name] = {'allOf': [
                    schema, additional_to_add
                ]}
    for property_name, schema in props_to_add.items():
        if property_name not in props_result:
            if additional_result is None:
                props_result[property_name] = schema
            else:
                props_result[property_name] = {'allOf': [
                    schema, additional_result
                ]}
    return props_result


def _merge_prefix_items(result: dict, to_add: dict) -> dict:
    # Must merge items and prefixItems together
    # Schema 1: a,   a,   a,   b...
    # Schema 2: c,   c,   d...
    # Result:   a+c, a+c, a+d, b+d...

    items_a = result.get('items', NORM_TRUE)
    prefix_items_a = result.get('prefixItems', [])
    items_b = to_add.get('items', NORM_TRUE)
    prefix_items_b = to_add.get('prefixItems', [])
    assert isinstance(prefix_items_a, list)
    assert isinstance(prefix_items_b, list)
    result_prefix_items = []

    if len(prefix_items_a) > len(prefix_items_b):
        prefix_items_b += [items_b] * \
            (len(prefix_items_a) - len(prefix_items_b))
    else:
        prefix_items_a += [items_a] * \
            (len(prefix_items_b) - len(prefix_items_a))

    assert len(prefix_items_a) == len(prefix_items_b)
    for i, j in zip(prefix_items_a, prefix_items_b):
        result_prefix_items.append({'allOf': [i, j]})

    return result_prefix_items


_complex_mergers = {
    'prefixItems': _merge_prefix_items,
    'properties': _merge_properties,
}


def _merge(result: dict, to_add: dict) -> None:

    for key, merger in _complex_mergers.items():
        if key in result or key in to_add:
            result[key] = merger(result, to_add)

    for key, value in result.items():
        if key not in to_add:
            # nothing to merge
            continue
        if key in _simple_mergers:
            merger = _simple_mergers[key]
            result[key] = merger(value, to_add[key])
        elif key in _complex_mergers:
            continue
        else:
            raise NormalizationException(f"Do not know how to merge '{key}'")

    # Copy all remaining keys
    for key, value in to_add.items():
        if key not in result and key not in _complex_mergers:
            result[key] = value

def merge(schemas: List[SchemaType]) -> SchemaType:
    return merge_simple(schemas)

def merge_simple(schemas: List[SchemaType]) -> SchemaType:
    assert len(schemas) > 0
    results = []
    num = max([len(s['anyOf']) for s in schemas])
    for idx in range(num):
        result = {}
        for schema in schemas:
            ao = schema['anyOf']
            if len(ao) == 0: continue
            option = ao[idx % len(ao)]
            _merge(result, option)
        results.append(result)
    return {'anyOf': results}


def merge_all(schemas: List[SchemaType]) -> SchemaType:
    assert len(schemas) > 0
    result = [{}]
    for schema in schemas:
        new_result = []
        for option in schema['anyOf']:
            for i in result:
                ii = i.copy()
                _merge(ii, option)
                new_result.append(ii)
        result = new_result
    return {'anyOf': result}


def _simplify_if_then_else(schema: dict, resolver: Resolver):
    # This is valid iff:
    # (not(IF) or THEN) and (IF or ELSE)
    # <==>
    #   ( IF and THEN ) or
    #   ( not(IF) and ELSE)

    # See https://json-schema.org/understanding-json-schema/reference/conditionals.html#implication
    if 'if' not in schema and 'then' not in schema and 'else' not in schema:
        return schema
    sub_schema = schema.copy()
    if_schema = sub_schema.pop('if', None)
    then_schema = sub_schema.pop('then', True)
    else_schema = sub_schema.pop('else', True)
    any_of = []
    if if_schema is not None and else_schema is not None:
        any_of.append({'allOf': [
            sub_schema,
            {'not': if_schema},
            else_schema
        ]})
    if if_schema is not None and then_schema is not None:
        any_of.append({'allOf': [
            sub_schema,
            if_schema,
            then_schema
        ]})
    if not any_of:
        return {'anyOf': [{}]}
    return {'anyOf': any_of}


def _inline_refs(schema: dict, resolver: Resolver) -> Tuple[dict, bool]:
    if schema is False:
        return NORM_FALSE, False

    if schema is True:
        return NORM_TRUE, True

    contains_refs = False

    if '$ref' in schema:
        side_schema = schema.copy()
        del side_schema['$ref']
        pointer = JsonPointer.from_string(schema['$ref'])
        ref_schema = resolver.resolve(pointer)
        schema = {'allOf': [side_schema, ref_schema]}
        contains_refs = True

    for kw in ['anyOf', 'allOf', 'oneOf']:
        for idx, sub_schema in enumerate(schema.get(kw, [])):
            (new_schema, new_contains_refs) = _inline_refs(sub_schema, resolver)
            schema[kw][idx] = new_schema
            contains_refs = contains_refs or new_contains_refs
    if 'not' in schema:
        (new_schema, new_contains_refs) = _inline_refs(schema['not'], resolver)
        schema['not'] = new_schema
        contains_refs = contains_refs or new_contains_refs

    return schema, contains_refs


def _to_dnf(schema: dict, resolver: Resolver, new_refs: Dict[str, dict]) -> dict:

    if schema is False:
        return NORM_FALSE

    if schema is True:
        return NORM_TRUE

    schema = _simplify_if_then_else(schema, resolver)

    # anyOf
    if 'anyOf' in schema:
        any_ofs = []
        for sub_schema in schema['anyOf']:
            normalized_sub_schema = _to_dnf(sub_schema, resolver, new_refs)
            any_ofs.extend(normalized_sub_schema['anyOf'])
    else:
        any_ofs = [{}]

    # oneOf
    if 'oneOf' in schema:
        one_ofs = []
        normalized_sub_schemas = [
            _to_dnf(sub_schema, resolver, new_refs)
            for sub_schema in schema['oneOf']
        ]
        for idx, _ in enumerate(normalized_sub_schemas):
            options = merge([
                invert(i) if i == idx else i
                for i in normalized_sub_schemas
            ])
            one_ofs.extend(options['anyOf'])
    else:
        one_ofs = [{}]

    # allOf
    all_ofs = []
    side_schema = schema.copy()
    for sub_schema in ['allOf', 'anyOf', 'oneOf', 'not']:
        if sub_schema in side_schema:
            del side_schema[sub_schema]
    all_ofs.append({'anyOf': [side_schema]})
    for sub_schema in schema.get('allOf', []):
        all_ofs.append(_to_dnf(sub_schema, resolver, new_refs))
    s = merge(all_ofs)

    return merge([
        {'anyOf': any_ofs},
        {'anyOf': one_ofs},
        s
    ])


def _normalize(schema: dict, resolver: Resolver, new_refs: Dict[str, dict]) -> dict:

    if schema is False:
        return NORM_FALSE

    if schema is True:
        return NORM_TRUE

    # Check cache (to avoid stack overflows due to recursive schemas)
    new_ref_name = hashlib.sha1(json.dumps(schema).encode()).hexdigest()
    if new_ref_name in new_refs:
        return {'anyOf': [{'$ref': f"#/$defs/{new_ref_name}"}]}

    # Inline all references (if any)
    (schema, contains_refs) = _inline_refs(schema, resolver)

    result = _to_dnf(schema, resolver, new_refs)

    # Store new schema if sub-schemas later try to reference it
    if contains_refs:
        new_refs[new_ref_name] = result

    # Iterate sub-schemas
    for sub_schema in result['anyOf']:
        for kw in ['additionalProperties', 'items', 'additionalItems']:
            if kw in sub_schema:
                sub_schema[kw] = _normalize(sub_schema[kw], resolver, new_refs)

        props: dict = sub_schema.get('properties', {})
        for name, sub_sub_schema in props.items():
            props[name] = _normalize(sub_sub_schema, resolver, new_refs)

        prefix_items: list = sub_schema.get('prefixItems', [])
        for idx, sub_sub_schema in enumerate(prefix_items):
            prefix_items[idx] = _normalize(sub_sub_schema, resolver)

    # Return
    if contains_refs:
        return {'anyOf': [{'$ref': f"#/$defs/{new_ref_name}"}]}
    else:
        return result


def normalize(schema: SchemaType) -> any:
    if schema is False:
        return {'type': []}

    if schema is True:
        return {}

    if not isinstance(schema, dict):
        raise NormalizationException(
            f"Schema must be of type bool or dict, got {type(schema)}")
    new_schema = schema.copy()
    for kw in ['$schema', '$defs']:
        if kw in schema:
            del new_schema[kw]
    resolver = Resolver(schema)
    new_refs: Dict[str, dict] = {}
    new_schema = _normalize(new_schema, resolver, new_refs)
    if '$schema' in schema:
        new_schema['$schema'] = schema['$schema']
    new_schema['$defs'] = new_refs
    return new_schema


def check_normalized(schema: SchemaType) -> None:
    resolver = Resolver(schema)
    checked_refs = set()
    _check_normalized(schema, resolver, checked_refs)


def _check_normalized(schema: SchemaType, resolver: Resolver, checked_refs: Set[str]) -> None:
    if not isinstance(schema, dict):
        raise NormalizationException(f"Must be a dict, got {schema}")

    keys = set(schema.keys())
    for i in ['$schema', '$defs']:
        if i in keys:
            keys.remove(i)
    if len(keys) != 1 or 'anyOf' not in keys:
        raise NormalizationException(
            f"Schema has one key not being anyOf, got {keys}")

    any_of = schema['anyOf']
    if not isinstance(any_of, list):
        raise NormalizationException(f"anyOf must be a list, is '{any_of}'")

    for idx, sub_schema in enumerate(any_of):

        # Disallowed keywords in any-of elements
        for i in ['anyOf', 'allOf', 'oneOf', 'not', 'if', 'then', 'else']:
            if i in sub_schema:
                raise NormalizationException(
                    f"'{i}' not allowed in normalized sub_schema {idx}")

        if '$ref' in sub_schema:
            if len(sub_schema.keys()) != 1:
                raise NormalizationException(
                    f"Sub-schema {idx} has other keys beside $ref")
            ref = sub_schema['$ref']
            if ref not in checked_refs:
                checked_refs.add(ref)
                pointer = JsonPointer.from_string(ref)
                _check_normalized(resolver.resolve(
                    pointer), resolver, checked_refs)

        # Traverse sub-schemas
        for kw in ['additionalProperties', 'items', 'additionalItems']:
            if kw in schema:
                _check_normalized(schema[kw], resolver, checked_refs)

        for i in sub_schema.get('properties', {}).values():
            _check_normalized(i, resolver, checked_refs)

        for i in sub_schema.get('prefixItems', []):
            _check_normalized(i, resolver, checked_refs)
