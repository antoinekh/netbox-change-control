"""
Rendering a branch diff for human review.

A branch diff stores related objects as raw primary keys, so an unrendered row reads
"provider 2 -> 3", which tells a reviewer nothing. This module turns a ChangeDiff into rows
with names substituted, batching the lookups.

This lives outside the view layer because it is domain logic, not presentation: checks and
integrations need the same rendering to describe a change.
"""

from django.core.exceptions import FieldDoesNotExist

__all__ = ('build_change_rows',)


def build_change_rows(diffs):
    """
    Return the changed attributes of each ChangeDiff, with foreign keys resolved to names.

    A branch diff stores related objects as raw primary keys, so an unresolved row reads
    "provider 2 -> 3", which tells a reviewer nothing. Every referenced object is looked up
    and shown by name, with its id kept alongside for traceability.

    Lookups are batched per model rather than per field, so a diff touching fifty objects
    costs one query per related model instead of one per value.
    """
    rows = {diff.pk: _raw_attribute_rows(diff) for diff in diffs}

    # Pass one: work out which related objects are referenced.
    wanted = {}
    for diff in diffs:
        model = diff.object_type.model_class()
        if model is None:
            continue
        for attr in rows[diff.pk]:
            related = _related_model(model, attr['field'])
            if related is None:
                continue
            attr['related_model'] = related
            for value in (attr['original'], attr['modified']):
                for pk in _as_pks(value):
                    wanted.setdefault(related, set()).add(pk)

    # Pass two: fetch them, one query per model.
    resolved = {}
    for related, pks in wanted.items():
        for obj in related.objects.filter(pk__in=pks):
            resolved[(related, obj.pk)] = str(obj)

    # Pass three: render.
    for attrs in rows.values():
        for attr in attrs:
            related = attr.pop('related_model', None)
            attr['original_display'] = _display(attr['original'], related, resolved)
            attr['modified_display'] = _display(attr['modified'], related, resolved)

    return rows


def _raw_attribute_rows(diff):
    """
    Return the changed attributes of a ChangeDiff as a list of dicts.

    This deliberately does not use ChangeDiff.modified_diff. That property reaches
    altered_in_current, which tests `k in self.original` without checking whether `original`
    is None. For an object created inside a branch `original` is None, so it raises
    TypeError. Computing the rows here keeps the tab working on created and deleted objects.
    """
    from core.choices import ObjectChangeActionChoices

    original = diff.original or {}
    modified = diff.modified or {}

    if diff.action == ObjectChangeActionChoices.ACTION_CREATE:
        fields = sorted(k for k, v in modified.items() if v not in (None, '', [], {}))
    elif diff.action == ObjectChangeActionChoices.ACTION_DELETE:
        fields = sorted(k for k, v in original.items() if v not in (None, '', [], {}))
    else:
        fields = sorted(k for k, v in modified.items() if k in original and v != original[k])

    return [
        {
            'field': field,
            'original': original.get(field),
            'modified': modified.get(field),
        }
        for field in fields
    ]


def _related_model(model, field_name):
    """
    Return the model referenced by `field_name`, or None if it is not a relation.
    """
    try:
        field = model._meta.get_field(field_name)
    except (FieldDoesNotExist, AttributeError):
        return None

    related = getattr(field, 'related_model', None)
    # Tags and generic relations do not resolve by primary key, so leave them alone.
    if related is None or field_name == 'tags':
        return None
    return related


def _as_pks(value):
    """
    Return the primary keys held in a serialised field value.
    """
    if isinstance(value, int):
        return [value]
    if isinstance(value, (list, tuple)):
        return [v for v in value if isinstance(v, int)]
    return []


def _display(value, related, resolved):
    """
    Render one value, substituting names for primary keys where possible.
    """
    if value in (None, '', [], {}):
        return None
    if related is None:
        return value

    if isinstance(value, (list, tuple)):
        return ', '.join(resolved.get((related, pk), f'#{pk}') for pk in value if isinstance(pk, int)) or None
    if isinstance(value, int):
        name = resolved.get((related, value))
        return f'{name} (#{value})' if name else f'#{value} (deleted)'
    return value
