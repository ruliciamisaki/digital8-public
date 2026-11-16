"""Use the Anime global-tone pipeline with virtual tile-average colours."""

from dataclasses import dataclass
import numpy as np
import anime_paint as anime


@dataclass(frozen=True)
class RecipeReference:
    colours: np.ndarray
    recipe_ids: np.ndarray
    tones: anime.ColourReference


def build_reference(pixels,ink,coverage,targets,penalties,settings):
    # Equal averages must not compete as different colours. Keep the preferred
    # layout/lowest-contrast recipe, with the original order as the tie breaker.
    positions={};colours=[];ids=[]
    for index,target in enumerate(targets):
        key=tuple(np.round(target,6))
        if key not in positions:
            positions[key]=len(ids);colours.append(target);ids.append(index)
        else:
            position=positions[key]
            if penalties[index]<penalties[ids[position]]:ids[position]=index
    colours=np.asarray(colours,dtype=np.float32)
    tones=anime.build_colour_reference(pixels,ink,coverage,colours,settings.anime_tolerance,
                                       settings.colour_groups,False)
    return RecipeReference(colours,np.asarray(ids,dtype=np.uint16),tones)


def choose(pixels,ink,coverage,reference,settings,spatial_scale=1):
    valid=(coverage>0)&~ink
    colours=reference.colours
    darkest=int(np.argmin(colours@np.array((.2126,.7152,.0722))))
    smoothed=anime.smooth_colours(pixels,ink,coverage,settings.anime_tolerance,
                                 settings.anime_radius,spatial_scale)
    chosen=anime.classify_colours(smoothed,valid,colours,reference.tones,darkest)
    if settings.anime_min_area>1:
        permitted=np.ones((len(colours),len(colours)),bool)
        selected=reference.tones.selected
        features=anime.matching_features(colours[selected])
        permitted[np.ix_(selected,selected)]=anime.matching_costs(features,features)<256
        chosen=anime.clean_colour_cells(chosen,np.ascontiguousarray(pixels),valid,
                   float(settings.anime_tolerance),int(settings.anime_min_area*spatial_scale**2),permitted)
    return reference.recipe_ids[chosen]
