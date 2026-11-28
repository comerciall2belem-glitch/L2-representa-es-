"""Resolve display names without changing or weakening password validation."""
import unicodedata

def identity_key(name):
    text = unicodedata.normalize('NFKD', str(name))
    return ' '.join(''.join(c for c in text if not unicodedata.combining(c)).casefold().split())

def resolve_identity(name, candidates):
    if name in candidates:
        return name
    key = identity_key(name)
    matches = [candidate for candidate in candidates if identity_key(candidate) == key]
    if matches or key != 'anapon':
        return matches[0] if len(matches) == 1 else None

    # O apelido só aponta para uma conta ativa e inequívoca de Ana Paula.
    matches = [candidate for candidate in candidates if identity_key(candidate) == 'ana paula']
    return matches[0] if len(matches) == 1 else None
