"""Reversible MCP naming only. Never translate dataset contents or receipts."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re


class SurfaceError(ValueError):
    pass


class Surface:
    def __init__(self, definitions, profile='canonical'):
        self.definitions = {t['name']: t for t in definitions}
        if profile == 'canonical':
            self.spec = {'format': 'definitio-surface-profile-v1', 'name': 'canonical', 'version': 1,
                         'tools': {n: n for n in self.definitions}, 'parameters': {}, 'enums': {}}
            raw = json.dumps(self.spec, sort_keys=True, separators=(',', ':')).encode()
        elif profile == 'surface-v1':
            raw = (Path(__file__).parent / 'surfaces/surface-v1.json').read_bytes()
            self.spec = json.loads(raw)
        else:
            raise SurfaceError('Unknown surface profile')
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.names = self.spec['tools']
        self.reverse = {v: k for k, v in self.names.items()}
        if set(self.names) != set(self.definitions) or len(self.reverse) != len(self.names):
            raise SurfaceError('Surface must map every tool exactly once')
        for name, fields in self.spec['parameters'].items():
            if name not in self.definitions or set(fields) != set(self.definitions[name]['inputSchema']['properties']) or len(set(fields.values())) != len(fields):
                raise SurfaceError('Surface parameters do not match the engine schema: ' + name)
        for name, fields in self.spec['enums'].items():
            for key, values in fields.items():
                schema = self.definitions[name]['inputSchema']['properties'][key]
                if set(values) != set(schema['enum']) or len(set(values.values())) != len(values):
                    raise SurfaceError('Surface enum is not bijective: ' + name + '.' + key)
        for name in ('gis_delete_dataset', 'gis_delete_field', 'destructive_preflight'):
            if self.names[name] != name or any(k != v for k, v in self.fields(name).items()):
                raise SurfaceError('Destructive operation names must remain explicit')

    @property
    def declaration(self):
        return {'name': self.spec['name'], 'version': self.spec['version'], 'sha256': self.sha256}

    def fields(self, name):
        return self.spec['parameters'].get(name, {})

    def text(self, value):
        # Only trusted descriptions/templates enter here, never user strings.
        def call(match):
            name, body = match.group(1), match.group(2)
            for key, alias in self.fields(name).items():
                body = re.sub(r'\b' + re.escape(key) + r'(?=\s*=)', alias, body)
                for before, after in self.spec['enums'].get(name, {}).get(key, {}).items():
                    body = re.sub(r'(?<==)' + re.escape(before) + r'(?=[,\s)]|$)', after, body)
            return self.names.get(name, name) + '(' + body + ')'
        value = re.sub(r'\b([a-z][a-z_]+)\(([^()]*)\)', call, value)
        return re.sub(r'\b(?:' + '|'.join(map(re.escape, sorted(self.names, key=len, reverse=True))) + r')\b',
                      lambda m: self.names[m[0]], value)

    def arguments(self, name, value, encode=False):
        if not isinstance(value, dict):
            raise SurfaceError('Tool arguments must be an object')
        fields = self.fields(name)
        mapping = fields if encode else {v: k for k, v in fields.items()}
        out = {}
        for key, item in value.items():
            if not encode and key in fields and fields[key] != key:
                raise SurfaceError('Use the published surface parameter: ' + fields[key])
            dest = mapping.get(key, key)
            if dest in out:
                raise SurfaceError('Duplicate parameter after surface mapping')
            out[dest] = copy.deepcopy(item)
        get = lambda k: fields.get(k, k) if encode else k
        for key, enum in self.spec['enums'].get(name, {}).items():
            k = get(key)
            if k in out:
                translation = enum if encode else {v: k for k, v in enum.items()}
                v = out[k]
                if not encode and isinstance(v, str) and v in enum and enum[v] != v:
                    raise SurfaceError('Use the published surface enum for ' + key)
                out[k] = translation.get(v, v) if isinstance(v, str) else v
        convert_name = (lambda n: self.names.get(n, n)) if encode else (lambda n: self.reverse.get(n, n))
        if name == 'gis_describe_algorithm' and isinstance(out.get(get('algorithm')), str):
            out[get('algorithm')] = convert_name(out[get('algorithm')])
        if name == 'gis_call' and isinstance(out.get(get('verb')), str):
            raw = out[get('verb')]
            canonical = raw if encode else self.canonical_name(raw)
            if get('arguments') in out:
                out[get('arguments')] = self.arguments(canonical, out[get('arguments')], encode)
            out[get('verb')] = convert_name(raw)
        if name == 'authorization_grant' and isinstance(out.get(get('verbs')), list):
            out[get('verbs')] = [convert_name(v) for v in out[get('verbs')]]
        if name in ('task_dry_run', 'task_plan') and isinstance(out.get(get('steps')), list):
            for step in out[get('steps')]:
                if not isinstance(step, dict) or not isinstance(step.get('verb'), str):
                    continue  # Engine owns the schema error for malformed steps.
                raw = step['verb']
                canonical = raw if encode else self.canonical_name(raw)
                if 'parameters' in step:
                    step['parameters'] = self.arguments(canonical, step['parameters'], encode)
                step['verb'] = convert_name(raw)
        return out

    def canonical_name(self, name):
        if name not in self.reverse:
            raise SurfaceError('Unknown tool or verb in the selected surface: ' + str(name))
        return self.reverse[name]

    def decode(self, name, arguments):
        canonical = self.canonical_name(name)
        return canonical, self.arguments(canonical, arguments)

    def schema(self, name, schema):
        result = copy.deepcopy(schema)
        fields = self.fields(name)
        def visit(node, root=False):
            if isinstance(node, dict):
                if 'description' in node:
                    node['description'] = self.text(node['description'])
                if root:
                    if 'properties' in node:
                        props = node['properties']
                        for key, entry in props.items():
                            enum = self.spec['enums'].get(name, {}).get(key)
                            if enum:
                                entry['enum'] = [enum[v] for v in entry['enum']]
                            if key in ('algorithm', 'verb') and 'enum' in entry:
                                entry['enum'] = [self.names.get(v, v) for v in entry['enum']]
                            if key == 'verbs' and 'enum' in entry.get('items', {}):
                                entry['items']['enum'] = [self.names.get(v, v) for v in entry['items']['enum']]
                        node['properties'] = {fields.get(k, k): v for k, v in props.items()}
                    if 'required' in node:
                        node['required'] = [fields.get(k, k) for k in node['required']]
                if 'enum' in node and node['enum'] and all(isinstance(v, str) and v in self.names for v in node['enum']):
                    node['enum'] = [self.names[v] for v in node['enum']]
                for k, v in node.items():
                    if k not in ('description', 'required', 'enum'):
                        if root and k in ('anyOf', 'oneOf', 'allOf'):
                            for branch in v:
                                visit(branch, True)
                        else:
                            visit(v)
            elif isinstance(node, list):
                for child in node:
                    visit(child)
        visit(result, True)
        return result

    def tool(self, definition):
        result = copy.deepcopy(definition)
        name = definition['name']
        result['name'] = self.names[name]
        result['title'] = self.text(result.get('title', name))
        result['description'] = self.text(result['description'])
        result['inputSchema'] = self.schema(name, result['inputSchema'])
        return result

    def result(self, payload):
        result = copy.deepcopy(payload)
        if self.spec['name'] == 'canonical':
            return result
        data = result.get('data', {})
        # Discovery and actionable hints are presentation. Immutable task/evidence
        # records, data fields, URI contents, digests and preview receipts stay exact.
        if isinstance(data.get('tool'), dict) and data['tool'].get('name') in self.names:
            data['tool'] = self.tool(data['tool'])
        if isinstance(data.get('capability'), dict):
            cap = data['capability']
            if cap.get('name') in self.names:
                cap['name'] = self.names[cap['name']]
        for verb in data.get('verbs', []):
            verb['name'] = self.names.get(verb['name'], verb['name'])
        for family in data.get('families', []):
            family['tools'] = [self.names.get(v, v) for v in family.get('tools', [])]
        if 'next' in data:
            data['next'] = [self.names.get(v, v) for v in data['next']]
        if 'describe_tool' in data:
            data['describe_tool'] = self.names.get(data['describe_tool'], data['describe_tool'])
        if 'plan_step' in data:
            p = data['plan_step']
            if 'example' in p:
                p['example'] = self.arguments('task_plan', p['example'], True)
            for key in ('request_id', 'authorization_id'):
                controls = p.get('control_fields_outside_parameters', {})
                if key in controls:
                    controls[key] = self.names.get(controls[key], controls[key])
        for action in result.get('next_actions', []):
            n = action.get('tool')
            if n in self.names:
                action['tool'] = self.names[n]
                for key in ('arguments', 'arguments_patch'):
                    if key in action:
                        action[key] = self.arguments(n, action[key], True)
        return result


def surface_failure(message):
    return {'ok': False, 'status': 'error', 'interface_version': 'definitio-agent-v1', 'data': {},
            'error': {'code': 'INVALID_ARGUMENT', 'message': message,
                      'details': {'rule': 'published_surface_names'}, 'execution_started': False},
            'next_actions': [], 'certification': 'NOT_CERTIFIED'}
