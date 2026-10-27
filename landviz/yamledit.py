"""Targeted edits to a YAML file that keep everything else byte-for-byte: comments, key order,
flow vs block style, blank lines.

The design file is written by the AI and by people; the 2D editor changes a handful of values
in it (a polygon, a point, a size). Re-dumping the whole file would throw away comments and
layout, so instead we find the exact text span of the value with PyYAML's composer (every node
carries start/end character offsets) and splice in the new value.

Paths are lists of mapping keys and sequence indices, e.g. ["elements", 3, "wings", 0, "footprint"].
For element lists, `element_path(text, id)` finds the index of the element with that id.
"""
from __future__ import annotations

import yaml


class YamlEditError(ValueError):
    pass


def _flow(value) -> str:
    """A one-line YAML rendering of a value (flow style)."""
    s = yaml.safe_dump(value, default_flow_style=True, sort_keys=False, width=1 << 30).strip()
    if s.endswith("\n..."):
        s = s[:-4].strip()
    if s.endswith("..."):
        s = s[:-3].strip()
    return s


def _node_at(root, path):
    """(node, parent, key-node-or-index) for a path; node is None if the last key is missing."""
    node, parent, last = root, None, None
    for i, key in enumerate(path):
        if isinstance(node, yaml.MappingNode):
            found = None
            for k, v in node.value:
                if k.value == str(key):
                    found = (k, v)
                    break
            if found is None:
                if i == len(path) - 1:
                    return None, node, key
                raise YamlEditError(f"no key {key!r} at {path[:i]}")
            parent, last, node = node, found[0], found[1]
        elif isinstance(node, yaml.SequenceNode):
            if not isinstance(key, int) or key >= len(node.value):
                raise YamlEditError(f"no item {key!r} at {path[:i]}")
            parent, last, node = node, key, node.value[key]
        else:
            raise YamlEditError(f"can't descend into a scalar at {path[:i]}")
    return node, parent, last


def _compose(text):
    root = yaml.compose(text)
    if root is None:
        raise YamlEditError("empty YAML document")
    return root


def element_path(text: str, element_id: str, list_key: str = "elements") -> list:
    """Path to the element with this id in a top-level list (e.g. elements)."""
    root = _compose(text)
    seq, _, _ = _node_at(root, [list_key])
    if not isinstance(seq, yaml.SequenceNode):
        raise YamlEditError(f"{list_key} is not a list")
    for i, item in enumerate(seq.value):
        if isinstance(item, yaml.MappingNode):
            for k, v in item.value:
                if k.value == "id" and v.value == element_id:
                    return [list_key, i]
    raise YamlEditError(f"no element with id {element_id!r}")


def _line_start(text, i):
    return text.rfind("\n", 0, i) + 1


def _indent_of(text, i):
    s = _line_start(text, i)
    j = s
    while j < len(text) and text[j] == " ":
        j += 1
    return j - s


def set_value(text: str, path: list, value) -> str:
    """Set the value at path (creating the last mapping key if it's missing)."""
    root = _compose(text)
    node, parent, last = _node_at(root, path)
    new = _flow(value)
    if node is not None:
        a, b = node.start_mark.index, node.end_mark.index
        if isinstance(node, (yaml.MappingNode, yaml.SequenceNode)) and not node.flow_style and isinstance(last, int):
            # A block collection that is a list item ("- id: x\n  kind: …"): the item becomes one flow line.
            end = b
            while end > a and text[end - 1] in " \n":
                end -= 1
            return text[:a] + new + text[end:]
        if isinstance(node, (yaml.MappingNode, yaml.SequenceNode)) and not node.flow_style:
            # A block collection: replace it with a flow value on the key's line.
            key_end = last.end_mark.index if hasattr(last, "end_mark") else a
            colon = text.index(":", key_end)
            end = b
            while end > colon and text[end - 1] in " \n":
                end -= 1
            return text[:colon + 1] + " " + new + text[end:]
        return text[:a] + new + text[b:]
    # Missing key: add it to the parent mapping.
    if parent.flow_style:
        close = parent.end_mark.index - 1  # the '}'
        while text[close] != "}":
            close -= 1
        sep = ", " if parent.value else ""
        return text[:close].rstrip() + f"{sep}{last}: {new}" + text[close:]
    first_key = parent.value[0][0]
    indent = " " * _indent_of(text, first_key.start_mark.index) if parent.value else "  "
    if parent.value and _line_start(text, first_key.start_mark.index) != first_key.start_mark.index - _indent_of(text, first_key.start_mark.index):
        # first key shares a line with "- " (a list item); siblings align with the key's column
        indent = " " * (first_key.start_mark.column)
    last_val = parent.value[-1][1]
    end = last_val.end_mark.index
    while end > 0 and text[end - 1] == "\n":
        end -= 1
    return text[:end] + f"\n{indent}{last}: {new}" + text[end:]


def delete_item(text: str, path: list) -> str:
    """Remove a sequence item (e.g. an element)."""
    root = _compose(text)
    node, parent, idx = _node_at(root, path)
    if not isinstance(parent, yaml.SequenceNode):
        raise YamlEditError("delete_item needs a path to a list item")
    if parent.flow_style:
        items = parent.value
        a = node.start_mark.index
        b = node.end_mark.index
        if idx + 1 < len(items):
            b = items[idx + 1].start_mark.index
        elif idx > 0:
            a = items[idx - 1].end_mark.index
        return text[:a] + text[b:]
    # Block list: remove from the item's "- " line to the next item (or the list's end).
    start = _line_start(text, node.start_mark.index)
    if idx + 1 < len(parent.value):
        end = _line_start(text, parent.value[idx + 1].start_mark.index)
    else:
        end = node.end_mark.index
        if not text[node.end_mark.index - 1:node.end_mark.index] == "\n":
            nl = text.find("\n", end)
            end = len(text) if nl < 0 else nl + 1
    return text[:start] + text[end:]


def append_item(text: str, path: list, value) -> str:
    """Append a flow-style item to a block list (creating the list key if missing)."""
    root = _compose(text)
    node, parent, last = _node_at(root, path)
    new = _flow(value)
    if node is None:
        return set_value(text, path, [value])
    if not isinstance(node, yaml.SequenceNode):
        raise YamlEditError("append_item needs a path to a list")
    if node.flow_style or not node.value:
        return set_value(text, path, [*yaml.safe_load(yaml.serialize(node)), value])
    first = node.value[0]
    col = _indent_of(text, first.start_mark.index)
    dash = text[_line_start(text, first.start_mark.index):first.start_mark.index]
    prefix = dash if dash.strip().startswith("-") else " " * col + "- "
    end = node.value[-1].end_mark.index
    while end > 0 and text[end - 1] == "\n":
        end -= 1
    return text[:end] + "\n" + prefix + new + text[end:]


def rename_key(text: str, path: list, old: str, new: str) -> str:
    """Rename a key in the mapping at path (keeps its value and position)."""
    root = _compose(text)
    node, _, _ = _node_at(root, path) if path else (root, None, None)
    if not isinstance(node, yaml.MappingNode):
        raise YamlEditError(f"{path} is not a mapping")
    for k, _v in node.value:
        if k.value == old:
            return text[:k.start_mark.index] + _flow(new) + text[k.end_mark.index:]
    return text


def has_path(text: str, path: list) -> bool:
    try:
        node, _, _ = _node_at(_compose(text), path)
        return node is not None
    except YamlEditError:
        return False


def set_deep(text: str, path: list, value) -> str:
    """set_value, creating missing parent mappings on the way."""
    for i in range(1, len(path)):
        if not has_path(text, path[:i]):
            nested = value
            for key in reversed(path[i:]):
                nested = {key: nested}
            return set_value(text, path[:i], nested)
    return set_value(text, path, value)
