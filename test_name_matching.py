import unittest

from text_utils import count_target_in_name


class NameMatchingTests(unittest.TestCase):
    def test_plain_telugu_asif(self):
        self.assertEqual(count_target_in_name("అసిఫ్", "Asif"), 1)

    def test_dotted_initial_asif(self):
        self.assertEqual(count_target_in_name("కె.అసీఫ్", "Asif"), 1)

    def test_multiple_dotted_initials_asif(self):
        self.assertEqual(count_target_in_name("పి.యమ్.డి.ఆసిఫ్", "Asif"), 1)

    def test_vasif_is_not_asif(self):
        self.assertEqual(count_target_in_name("వాసిఫ్", "Asif"), 0)

    def test_dotted_initial_and_full_name(self):
        self.assertEqual(count_target_in_name("పి.హసీనా", "P. Haseena"), 1)

    def test_unrelated_initial_does_not_match(self):
        self.assertEqual(count_target_in_name("ఎం.హసీనా", "P. Haseena"), 0)

    def test_naseembanu_without_initial_matches_prefixed_name(self):
        self.assertEqual(count_target_in_name("పి.నసీంబాను", "Naseembanu"), 1)

    def test_naseem_does_not_equal_naseembanu(self):
        self.assertEqual(count_target_in_name("పి.నసీంబాను", "Naseem"), 0)

    def test_naseem_does_not_match_nearby_different_names(self):
        for different_name in ["నాజీమ", "నాసిర్", "నాసిక్", "ఆసిమ"]:
            with self.subTest(name=different_name):
                self.assertEqual(count_target_in_name(different_name, "Naseem"), 0)

    def test_nasirabanu_is_not_naseembanu(self):
        self.assertEqual(count_target_in_name("టి.నాసిరాబాను", "Naseembanu"), 0)

    def test_kalavathi_common_latin_spelling_matches_telugu(self):
        self.assertEqual(count_target_in_name("పి.కళావతి", "Kalavathi"), 1)


if __name__ == "__main__":
    unittest.main()
