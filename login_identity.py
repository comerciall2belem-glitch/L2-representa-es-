"""Resolve display names without changing or weakening password validation."""
import unicodedata

def identity_key(name):
    text = unicodedata.normalize('NFKD', str(name))
    return ' '.join(''.join(c for c in text if not unicodedata.combining(c)).casefold().split())

def resolve_identity(name, candidates):
    if name in candidates:
        return name
    matches = [candidate for candidate in candidates if identity_key(candidate) == identity_key(name)]
    return matches[0] if len(matches) == 1 else None
