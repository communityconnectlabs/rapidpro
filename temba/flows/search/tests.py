from temba.flows.models import FlowRun
from temba.flows.search.parser import FlowRunSearch
from temba.tests import TembaTest
from temba.tests.engine import MockSessionWriter


class FlowRunSearchTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.contact = self.create_contact("Eric", phone="+250788382382")
        self.contact2 = self.create_contact("Bob", phone="+250788382383")

        self.flow = self.get_flow("color_v13")
        flow_nodes = self.flow.get_definition()["nodes"]
        self.color_prompt = flow_nodes[0]
        self.color_split = flow_nodes[4]

    def _create_run_with_result(self, contact, result_name, value, category, input_text):
        msg = self.create_incoming_msg(contact, input_text)
        (
            MockSessionWriter(contact, self.flow)
            .visit(self.color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(self.color_split)
            .wait()
            .resume(msg=msg)
            .set_result(result_name, value, category, input_text)
            .complete()
            .save()
        )

    def test_flow_search(self):
        self._create_run_with_result(self.contact, "Color", "blue", "Blue", "blue")

        queryset = FlowRun.objects.all()

        run_search = FlowRunSearch(query="Color=blue", base_queryset=queryset)
        queryset, e = run_search.search()

        self.assertEqual(len(queryset), 1)

    def test_search_with_contains_operator(self):
        """Test ~ operator (icontains)."""
        self._create_run_with_result(self.contact, "Color", "blue sky", "Blue", "blue sky")

        queryset = FlowRun.objects.all()
        run_search = FlowRunSearch(query="Color~blue", base_queryset=queryset)
        result, e = run_search.search()
        self.assertEqual(len(result), 1)
        self.assertEqual(e, "")

    def test_search_with_not_equal_operator(self):
        """Test != (<>) operator."""
        self._create_run_with_result(self.contact, "Color", "blue", "Blue", "blue")
        self._create_run_with_result(self.contact2, "Color", "red", "Red", "red")

        queryset = FlowRun.objects.all()
        run_search = FlowRunSearch(query="Color!=blue", base_queryset=queryset)
        result, e = run_search.search()
        # not equal should exclude "blue"
        self.assertEqual(e, "")

    def test_search_with_and_operator(self):
        """Test AND condition."""
        self._create_run_with_result(self.contact, "Color", "blue", "Blue", "blue")

        queryset = FlowRun.objects.all()
        run_search = FlowRunSearch(query="Color=blue AND Color=blue", base_queryset=queryset)
        result, e = run_search.search()
        self.assertEqual(len(result), 1)
        self.assertEqual(e, "")

    def test_search_with_or_operator(self):
        """Test OR condition."""
        self._create_run_with_result(self.contact, "Color", "blue", "Blue", "blue")

        queryset = FlowRun.objects.all()
        run_search = FlowRunSearch(query="Color=blue OR Color=red", base_queryset=queryset)
        result, e = run_search.search()
        self.assertEqual(len(result), 1)
        self.assertEqual(e, "")

    def test_search_with_not_operator(self):
        """Test NOT condition."""
        self._create_run_with_result(self.contact, "Color", "blue", "Blue", "blue")
        self._create_run_with_result(self.contact2, "Color", "red", "Red", "red")

        queryset = FlowRun.objects.all()
        run_search = FlowRunSearch(query="NOT Color=blue", base_queryset=queryset)
        result, e = run_search.search()
        self.assertEqual(e, "")

    def test_search_with_empty_value(self):
        """Test searching for empty value adds isnull filter."""
        self._create_run_with_result(self.contact, "Color", "blue", "Blue", "blue")

        queryset = FlowRun.objects.all()
        run_search = FlowRunSearch(query="Color=", base_queryset=queryset)
        result, e = run_search.search()
        self.assertEqual(e, "")

    def test_search_with_parentheses_error(self):
        """Test parentheses produce error."""
        queryset = FlowRun.objects.all()
        run_search = FlowRunSearch(query="(Color=blue)", base_queryset=queryset)
        result, e = run_search.search()
        self.assertIn("not allowed", e)

    def test_search_with_multi_word_value(self):
        """Test multi-word values are joined correctly."""
        self._create_run_with_result(self.contact, "Color", "light blue", "Blue", "light blue")

        queryset = FlowRun.objects.all()
        run_search = FlowRunSearch(query="Color=light blue", base_queryset=queryset)
        result, e = run_search.search()
        self.assertEqual(e, "")

    def test_search_no_results(self):
        """Test search with no matching results."""
        queryset = FlowRun.objects.all()
        run_search = FlowRunSearch(query="Color=purple", base_queryset=queryset)
        result, e = run_search.search()
        self.assertEqual(len(result), 0)
        self.assertEqual(e, "")

    def test_preprocess_query(self):
        """Test _preprocess_query directly."""
        # basic
        self.assertIn("=", FlowRunSearch._preprocess_query("Color=blue"))
        # not equal
        self.assertIn("<>", FlowRunSearch._preprocess_query("Color!=blue"))
        # contains
        self.assertIn("~", FlowRunSearch._preprocess_query("Color~blue"))
