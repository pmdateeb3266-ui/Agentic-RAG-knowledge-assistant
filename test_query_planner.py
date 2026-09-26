import unittest

from query_analyzer import analyze_query


class QueryPlannerRegressionTests(unittest.TestCase):
    def test_house_lookup_is_exhaustive(self):
        plan = analyze_query("Who is in house number 9-31")
        self.assertEqual(plan["intent"], "STRUCTURED_QUERY")
        self.assertIn({"field": "house_number", "op": "eq", "value": "9-31"}, plan["filters"])

    def test_house_suffix_is_not_treated_as_name(self):
        plan = analyze_query("How many people live in the house number 9-280-A")
        self.assertEqual(plan["intent"], "STRUCTURED_QUERY")
        self.assertEqual(plan["aggregation"], "count")
        self.assertEqual(
            plan["filters"],
            [{"field": "house_number", "op": "eq", "value": "9-280-A"}],
        )
        self.assertIsNone(plan["target"])

    def test_long_house_suffix_is_not_treated_as_name(self):
        plan = analyze_query("Who lives in house number 9-280-AB")
        self.assertEqual(
            plan["filters"],
            [{"field": "house_number", "op": "eq", "value": "9-280-AB"}],
        )
        self.assertIsNone(plan["target"])

    def test_age_filter_uses_explicit_pagination(self):
        plan = analyze_query("Show details of people age 70 and above page 2")
        self.assertIn({"field": "age", "op": "gte", "value": 70}, plan["filters"])
        self.assertEqual(plan["pagination"], {"page": 2, "show_all": False})

    def test_show_all(self):
        plan = analyze_query("Show all details of people age 70 and above")
        self.assertTrue(plan["pagination"]["show_all"])

    def test_compound_ranking_and_english(self):
        plan = analyze_query("Give the oldest 5 people and youngest 5 people in English")
        self.assertEqual(plan["output_language"], "en")
        self.assertTrue(plan["output_language_explicit"])
        self.assertIsNone(plan["target"])
        self.assertEqual(
            plan["operations"],
            [
                {"name": "oldest", "order_by": "age", "order_direction": "desc", "limit": 5},
                {"name": "youngest", "order_by": "age", "order_direction": "asc", "limit": 5},
            ],
        )

    def test_total_voters_is_deterministic_count(self):
        plan = analyze_query("How many total voters are listed")
        self.assertEqual(plan["intent"], "STRUCTURED_QUERY")
        self.assertEqual(plan["aggregation"], "count")
        self.assertEqual(plan["filters"], [])

    def test_polling_booths_use_document_header_entity(self):
        plan = analyze_query("how many polling booths are listed")
        self.assertEqual(plan["intent"], "STRUCTURED_QUERY")
        self.assertEqual(plan["entity"], "polling_booth")
        self.assertEqual(plan["aggregation"], "count")
        self.assertEqual(plan["filters"], [])

    def test_polling_booth_address_projection(self):
        plan = analyze_query("Show all polling booth addresses")
        self.assertEqual(plan["entity"], "polling_booth")
        self.assertEqual(plan["requested_fields"], ["address"])
        self.assertEqual(plan["filters"], [])

    def test_specific_polling_booth_details(self):
        plan = analyze_query("Show complete details for polling booth 92")
        self.assertEqual(plan["entity_filters"], [{"field": "number", "op": "eq", "value": "92"}])
        self.assertIn("address", plan["requested_fields"])

    def test_plain_booth_details_are_document_entity(self):
        plan = analyze_query("Complete details for booth 95")
        self.assertEqual(plan["entity"], "polling_booth")
        self.assertEqual(plan["entity_filters"], [{"field": "number", "op": "eq", "value": "95"}])
        self.assertIsNone(plan["target"])

    def test_plain_house_word_extracts_arbitrary_identifier(self):
        plan = analyze_query("Residents of house 10/221")
        self.assertEqual(plan["filters"], [{"field": "house_number", "op": "eq", "value": "10/221"}])
        self.assertIsNone(plan["target"])

    def test_age_range_has_no_people_name_filter(self):
        plan = analyze_query("People age between 18 to 28")
        self.assertEqual(plan["filters"], [{"field": "age", "op": "between", "min": 18, "max": 28}])
        self.assertIsNone(plan["target"])

    def test_arbitrary_age_range_and_gender_group(self):
        plan = analyze_query("How many male and female voters are between ages 31 and 47?")
        self.assertEqual(plan["filters"], [{"field": "age", "op": "between", "min": 31, "max": 47}])
        self.assertEqual(plan["aggregation"], "count")
        self.assertEqual(plan["group_by"], "gender")
        self.assertIsNone(plan["target"])

    def test_content_topic_query_routes_to_semantic_search(self):
        plan = analyze_query("Find key topics")
        self.assertEqual(plan["intent"], "SEMANTIC")
        self.assertEqual(plan["filters"], [])
        self.assertIsNone(plan["target"])

    def test_document_page_total(self):
        plan = analyze_query("How many total pages are indexed?")
        self.assertEqual(plan["entity"], "document")
        self.assertEqual(plan["entity_operation"], "sum_page_count")

    def test_summary_command_is_not_a_person_name(self):
        plan = analyze_query("Summarize all documents")
        self.assertEqual(plan["intent"], "DOCUMENT_SUMMARY")
        self.assertIsNone(plan["target"])

    def test_total_male_female_is_grouped_not_filtered(self):
        plan = analyze_query("How many total, male and female voters are listed")
        self.assertEqual(plan["intent"], "STRUCTURED_QUERY")
        self.assertEqual(plan["aggregation"], "count")
        self.assertEqual(plan["group_by"], "gender")
        self.assertEqual(plan["filters"], [])

    def test_specific_pdf_scope(self):
        plan = analyze_query("Show serial number 14 from 17576784761392.pdf")
        self.assertEqual(
            plan["document_scope"],
            {"mode": "specific", "source": "17576784761392.pdf", "sources": ["17576784761392.pdf"], "explicit": True},
        )

    def test_specific_pdf_scope_with_sentence_period(self):
        plan = analyze_query("List everyone in house 10-511 from 17576784782290.pdf.")
        self.assertIsNone(plan["entity"])
        self.assertEqual(plan["document_scope"]["source"], "17576784782290.pdf")
        self.assertEqual(plan["filters"], [{"field": "house_number", "op": "eq", "value": "10-511"}])

    def test_pdf_filename_is_scope_not_document_entity(self):
        plan = analyze_query("How many voters are in 17576784782290.pdf?")
        self.assertEqual(plan["intent"], "STRUCTURED_QUERY")
        self.assertIsNone(plan["entity"])
        self.assertEqual(plan["aggregation"], "count")
        self.assertEqual(plan["document_scope"]["source"], "17576784782290.pdf")

    def test_document_scoped_age_and_gender_query(self):
        plan = analyze_query("How many men aged 40 to 55 are in 17579156874769.pdf?")
        self.assertIsNone(plan["entity"])
        self.assertEqual(plan["document_scope"]["source"], "17579156874769.pdf")
        self.assertIn({"field": "age", "op": "between", "min": 40, "max": 55}, plan["filters"])
        self.assertIn({"field": "gender", "op": "eq", "value": "male"}, plan["filters"])

    def test_single_oldest_and_youngest_rankings(self):
        oldest = analyze_query("Show the oldest 5 voters.")
        youngest = analyze_query("Show the youngest 5 voters.")
        self.assertEqual((oldest["order_by"], oldest["order_direction"], oldest["limit"]), ("age", "desc", 5))
        self.assertEqual((youngest["order_by"], youngest["order_direction"], youngest["limit"]), ("age", "asc", 5))

    def test_epic_label_is_not_a_name(self):
        plan = analyze_query("Show details for EPIC AP251724255701.")
        self.assertIsNone(plan["target"])
        self.assertEqual(plan["filters"], [{"field": "epic_id", "op": "eq", "value": "AP251724255701"}])

    def test_explicit_all_document_scope(self):
        plan = analyze_query("Show people age 70 and above from all documents")
        self.assertEqual(plan["document_scope"], {"mode": "all", "source": None, "explicit": True})

    def test_csv_export_implies_show_all(self):
        plan = analyze_query("Show people age 70 and above and export to CSV")
        self.assertEqual(plan["export_format"], "csv")
        self.assertTrue(plan["pagination"]["show_all"])
        self.assertIsNone(plan["target"])
        self.assertEqual(plan["filters"], [{"field": "age", "op": "gte", "value": 70}])

    def test_export_all_voters_is_structured(self):
        plan = analyze_query("Export all voters to CSV")
        self.assertEqual(plan["intent"], "STRUCTURED_QUERY")
        self.assertEqual(plan["export_format"], "csv")
        self.assertIsNone(plan["target"])
        self.assertEqual(plan["filters"], [])
        self.assertTrue(plan["pagination"]["show_all"])

    def test_explicit_answer_language(self):
        plan = analyze_query("How many total voters are listed in Hindi")
        self.assertEqual(plan["output_language"], "hi")
        self.assertTrue(plan["output_language_explicit"])
        self.assertIsNone(plan["target"])

    def test_same_house_is_two_step_operation(self):
        plan = analyze_query("Find Asif and other people related to Asif, as in same house number that of Asif.")
        self.assertEqual(plan["intent"], "STRUCTURED_QUERY")
        self.assertNotIn({"field": "house_number", "op": "eq", "value": "that"}, plan["filters"])
        self.assertEqual(plan["operations"], [{"name": "same_household", "target": "Asif"}])
        self.assertFalse(any(item.get("field") == "name" for item in plan["filters"]))

    def test_largest_household_is_deterministic_operation(self):
        plan = analyze_query("Which house number has more people in it")
        self.assertEqual(plan["intent"], "STRUCTURED_QUERY")
        self.assertEqual(
            plan["operations"],
            [{"name": "largest_household", "group_by": "source_and_house_number", "include_ties": True}],
        )
        self.assertEqual(plan["filters"], [])
        self.assertIsNone(plan["target"])

    def test_largest_household_wording_is_generalized(self):
        for question in [
            "Which house contains the most residents?",
            "Show the house with the highest number of voters",
            "Find the largest household",
        ]:
            with self.subTest(question=question):
                plan = analyze_query(question)
                self.assertEqual(plan["intent"], "STRUCTURED_QUERY")
                self.assertEqual(plan["operations"][0]["name"], "largest_household")

    def test_family_members_are_resolved_through_seed_household(self):
        plan = analyze_query("Give details of all family members related to P. Haseena in English")
        self.assertEqual(plan["intent"], "STRUCTURED_QUERY")
        self.assertEqual(plan["target"], "P. Haseena")
        self.assertEqual(plan["operations"], [{"name": "same_household", "target": "P. Haseena"}])
        self.assertFalse(any(item.get("field") == "name" for item in plan["filters"]))
        self.assertEqual(plan["output_language"], "en")

    def test_lowercase_family_name_is_captured(self):
        plan = analyze_query("find family members related to asif")
        self.assertEqual(plan["target"], "asif")
        self.assertEqual(plan["operations"], [{"name": "same_household", "target": "asif"}])

    def test_plural_voters_name_uses_voter_field_only(self):
        plan = analyze_query("Find all Asif in voters name and provide details of Asif")
        name_filter = next(item for item in plan["filters"] if item.get("field") == "name")
        self.assertEqual(name_filter["scope"], "voter_name")

    def test_find_name_wherever_listed_is_structured(self):
        plan = analyze_query("Find Asif where ever listed and provide data of related people also")
        self.assertEqual(plan["intent"], "STRUCTURED_QUERY")
        self.assertEqual(plan["target"], "Asif")
        self.assertEqual(plan["structured_scope"], "all_name_fields")
        self.assertTrue(any(item.get("field") == "name" for item in plan["filters"]))

    def test_find_full_multi_part_name(self):
        plan = analyze_query("Find P. Haseena")
        self.assertEqual(plan["intent"], "STRUCTURED_QUERY")
        self.assertEqual(plan["target"], "P. Haseena")
        self.assertEqual(plan["filters"][0]["value"], "P. Haseena")


if __name__ == "__main__":
    unittest.main()
