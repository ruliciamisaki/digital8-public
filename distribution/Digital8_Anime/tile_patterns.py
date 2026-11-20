"""User-supplied logical tiles; PC-8801 doubles rows only when displaying."""

from itertools import combinations
import numpy as np

EXTRA_TEMPLATES = {
    'eighth_staggered': ('BAAA','AAAA','AABA','AAAA'),
    'three_112_stripes': ('ACBC',),
    'three_112_alternate': ('ACBC','BCAC'),
    'three_112_diagonal': ('ACBC','CBCA','BCAC','CACB'),
    'three_111_stripes': ('CAB',),
    'three_111_diagonal': ('CAB','ABC','BCA'),
    'four_colour': ('ABCB', 'BCDC'),
    'five_colour': ('ABCD', 'DADE', 'CBAD', 'DADE'),
}
EIGHTH_TONES = ('eighth','eighth_staggered')
THREE_112 = ('none','three_112_stripes','three_112_alternate','three_112_diagonal')
THREE_111 = ('none','three_111_stripes','three_111_diagonal')


def validate(eighth, three_112, three_111, four_colour=False, five_colour=False):
    if eighth not in EIGHTH_TONES or three_112 not in THREE_112 or three_111 not in THREE_111:
        raise ValueError('invalid eighth-tone or three-colour pattern')
    if type(four_colour) is not bool or type(five_colour) is not bool:
        raise ValueError('four- and five-colour pattern switches must be bool')


def render(recipe_ids, ink, recipes, ink_index):
    """Resolve tile phases in bounded row batches with one lookup per pixel."""
    height, width = recipe_ids.shape
    sizes = np.asarray([recipe.shape for recipe in recipes], dtype=np.intp)
    tiles = np.zeros((len(recipes), max(len(recipe.tile) for recipe in recipes)), dtype=np.uint8)
    for index, recipe in enumerate(recipes):
        tiles[index, :len(recipe.tile)] = recipe.tile
    output = np.empty(recipe_ids.shape, dtype=np.uint8)
    x = np.arange(width)[None, :]
    for top in range(0, height, 128):
        bottom = min(top + 128, height)
        ids = recipe_ids[top:bottom]
        rows, columns = sizes[:, 0][ids], sizes[:, 1][ids]
        y = np.arange(top, bottom)[:, None]
        output[top:bottom] = tiles[ids, (y % rows) * columns + x % columns]
    output[ink] = ink_index
    return output


def extras(palette, recipe_type, eighth, three_112, three_111, observed_pairs=None,
           *, four_colour=False, five_colour=False):
    validate(eighth, three_112, three_111, four_colour, five_colour)
    colours=np.asarray(palette,dtype=np.float32)
    unique=[];seen=set()
    for index,colour in enumerate(colours):
        key=tuple(colour)
        if key not in seen:unique.append(index);seen.add(key)

    def recipe(name, a, b, c=None, d=None, e=None):
        rows = EXTRA_TEMPLATES[name]
        mapping = dict(zip('ABCDE', (a, b, c, d, e)))
        tile=tuple(mapping[char]for row in rows for char in row)
        target=tuple(float(v)for v in colours[np.asarray(tile)].mean(axis=0))
        used=sorted(set(tile))
        contrast=max(float(np.sum((colours[x]-colours[y])**2))for x,y in combinations(used,2))
        penalty=contrast*.002
        if c is None:
            if name!=eighth:penalty+=500.
            if observed_pairs is not None and tuple(sorted((a,b)))not in observed_pairs:penalty+=240.
        else:
            penalty+=120.  # Prefer two colours when they give the same average.
        return recipe_type(name,a,b,tile,(len(rows),len(rows[0])),target,penalty,c)

    result=[recipe('eighth_staggered',a,b)for a in unique for b in unique if a!=b]
    brightness=colours@np.array((.2126,.7152,.0722),dtype=np.float32)
    def ordered(values):return sorted(values,key=lambda i:(-brightness[i],i))
    if three_112!='none':
        for c in unique:
            # A/B have equal coverage; choose one stable polarity, as for 1:1.
            for a,b in combinations([i for i in unique if i!=c],2):
                a,b=ordered((a,b))
                result.append(recipe(three_112,a,b,c))
    if three_111!='none':
        for values in combinations(unique,3):result.append(recipe(three_111,*ordered(values)))
    # Use one stable assignment per colour set, with the published examples'
    # brightness order. This keeps the 16-colour palette search manageable.
    if four_colour:
        for values in combinations(unique, 4):
            b, c, d, a = ordered(values)
            result.append(recipe('four_colour', a, b, c, d))
    if five_colour:
        for values in combinations(unique, 5):
            d, b, a, c, e = ordered(values)
            result.append(recipe('five_colour', a, b, c, d, e))
    return tuple(result)
