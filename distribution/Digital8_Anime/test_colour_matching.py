import unittest
from dataclasses import replace

import numpy as np

import anime_paint as anime
import digital8_converter as converter


class ColourMatchingTests(unittest.TestCase):
    def test_bright_warm_skin_does_not_choose_similarly_saturated_blue(self):
        # Exact region mean from the reported PNG (brightness 1.05,
        # saturation 1.25, contrast 1.15). The former metric ranked pale blue
        # ahead of skin even with the whole TE saturated database available.
        skin = np.array(((254.646967,227.148153,200.387340),
                         (253.932591,225.418028,205.116833),
                         (255,200,180), (251.88,205.15,187.39), (254,217,195)))
        for palette_id in ('te','te_saturated','cw','cw_saturated'):
            palette = converter.palette_for('anime',palette_id).array()
            for limit in (0,24):
                selected = anime.select_palette(skin, np.ones(len(skin)), palette, limit,
                                                ink_index=0, trace_support=True)
                scores = anime.matching_costs(anime.matching_features(skin),
                                             anime.matching_features(palette[selected]))
                output = palette[selected[np.argmin(scores,axis=1)]].astype(int)
                with self.subTest(palette=palette_id, limit=limit):
                    self.assertTrue(np.all(output[:,0] > output[:,1]))
                    self.assertTrue(np.all(output[:,0] - output[:,2] > 20))

    def test_reported_recipe_keeps_skin_warm_and_armour_neutral(self):
        source = converter.ROOT / 'output_sample/anime/source/00505-1900052826.png'
        if not source.exists():
            self.skipTest('Repository reference image is not included in this distribution')
        options = replace(converter.anime_standard(), palette_id='te_saturated',
                          brightness=1.05, saturation=1.25, contrast=1.15,
                          anime_tolerance=100, line_growth=0, smooth_2x=False)
        image, _alpha = converter.convert_image(source, options)
        output = np.asarray(image.convert('RGB'))
        # Global tones use the local corrected highlight, not the former
        # whole-face average: neutral white is allowed, opposing blue is not.
        colour = output[440,650].astype(int)
        self.assertGreaterEqual(colour[0], colour[2])
        for x,y in ((650,745),(603,1004),(590,1215)):
            lab = anime.srgb_to_oklab(output[y,x])
            self.assertLess(float(np.linalg.norm(lab[1:])), .04)

    def test_second_reported_recipe_with_supersampling(self):
        source = converter.ROOT / 'output_sample/anime/source/00505-1900052826.png'
        if not source.exists():
            self.skipTest('Repository reference image is not included in this distribution')
        options = replace(converter.anime_standard(), palette_id='te_saturated',
                          brightness=1.05, saturation=1.0, contrast=1.1,
                          anime_tolerance=100, line_growth=0, smooth_2x=True)
        image, _alpha = converter.convert_image(source, options)
        output = np.asarray(image.convert('RGB'))
        colour = output[440,650].astype(int)
        self.assertGreaterEqual(colour[0], colour[2])
        np.testing.assert_array_equal(output[745,650], (255,255,255))

    def test_opposing_hue_loses_to_compatible_or_neutral_paints(self):
        sources = anime.matching_features(np.array(((254,225,205),(190,200,245))))
        paints = np.array(((225,229,252),(232,177,166),(255,255,255),(0,0,0)))
        costs = anime.matching_costs(sources, anime.matching_features(paints))
        np.testing.assert_array_equal(paints[np.argmin(costs,axis=1)],
                                      ((232,177,166),(225,229,252)))
        # If no warm paint is available, white beats the opposing blue tint.
        np.testing.assert_array_equal(paints[[0,2,3]][np.argmin(costs[0,[0,2,3]])], (255,255,255))
        # A restricted palette with only opposing colours still has a finite result.
        self.assertTrue(np.isfinite(costs).all())

    def test_costs_do_not_change_with_palette_subsets_or_batches(self):
        rgb = np.array(((0,0,0),(255,255,255),(255,200,180),(205,195,195),(30,60,220)))
        sources = anime.matching_features(rgb)
        targets = anime.matching_features(converter.palette_for('anime','te_saturated').array())
        full = anime.matching_costs(sources,targets)
        subset = (0,6,8,10,30,99)
        np.testing.assert_array_equal(full[:,subset],anime.matching_costs(sources,targets[list(subset)]))
        np.testing.assert_array_equal(full,np.concatenate([anime.matching_costs(sources[i:i+1],targets)
                                                          for i in range(len(sources))]))

    def test_primary_and_secondary_colour_families_survive_the_palette_budget(self):
        colours = np.array(((230,40,40),(40,210,50),(35,65,235),
                            (230,200,35),(210,40,210),(40,210,210)))
        for palette_id in ('te','te_saturated','cw','cw_saturated'):
            palette = converter.palette_for('anime',palette_id).array()
            selected = anime.select_palette(colours,np.ones(6),palette,24,0,True)
            scores = anime.matching_costs(anime.matching_features(colours),
                                         anime.matching_features(palette[selected]))
            output = palette[selected[np.argmin(scores,axis=1)]]
            source_lab, target_lab = anime.srgb_to_oklab(colours),anime.srgb_to_oklab(output)
            self.assertTrue(np.all(np.sum(source_lab[:,1:]*target_lab[:,1:],axis=1)>0))

    def test_neutral_armour_means_share_a_neutral_paint(self):
        # Measured region means from the user's white/grey armour. Plain OKLab
        # chose pink [226,182,173] for the hip/thigh and purple for the abdomen.
        means = np.array(((218.09,206.83,206.89), (212.72,201.69,202.08),
                          (213.84,202.45,202.71), (198.29,188.99,189.89),
                          (205.36,194.91,195.07), (179.73,166.45,169.13),
                          (251.88,205.15,187.39)))
        palette = converter.palette_for('anime', 'te').array()
        for limit in (0, 24):
            selected = anime.select_palette(means, np.ones(len(means)), palette, limit,
                                            ink_index=0, trace_support=True)
            scores = anime.matching_costs(anime.matching_features(means),
                                         anime.matching_features(palette[selected]))
            output = palette[selected[np.argmin(scores, axis=1)]]
            np.testing.assert_array_equal(output[:5], np.tile((199,224,231), (5,1)))
            np.testing.assert_array_equal(output[5], (139,146,154))
            self.assertGreater(output[6,0] - int(output[6,1]), 30)

    def test_exact_palette_colours_remain_exact_with_no_budget_limit(self):
        palette = converter.palette_for('anime', 'cw').array()
        features = anime.matching_features(palette)
        scores = anime.matching_costs(features,features)
        np.testing.assert_array_equal(palette[np.argmin(scores,axis=1)], palette)

    def test_same_metric_is_used_in_end_to_end_budgeted_paint(self):
        palette = converter.palette_for('anime', 'te').array()
        rgb = np.zeros((12, 49, 3), np.uint8)
        rgb[:, :12] = (218,207,207)
        rgb[:, 13:25] = (198,189,190)
        rgb[:, 26:38] = (205,195,195)
        rgb[:, 39:] = (252,205,187)
        ink = np.zeros(rgb.shape[:2], bool)
        ink[:, [12,25,38]] = True
        selected = []
        indices = anime.paint(rgb, ink, palette, max_colours=24, tolerance=128,
                              trace_support=True, palette_out=selected)
        output = palette[indices]
        for x in (4,18,30):
            np.testing.assert_array_equal(output[6,x], (199,224,231))
        self.assertLessEqual(len(np.unique(indices)),24)
        self.assertGreater(int(output[6,44,0]) - int(output[6,44,1]),30)


if __name__ == '__main__':
    unittest.main()
