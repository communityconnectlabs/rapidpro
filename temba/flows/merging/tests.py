import copy
import pickle

from temba.flows.merging import Graph, GraphDifferenceMap, Node
from temba.flows.merging.helpers import actions_names, get_flow_step_name, get_flow_step_type
from temba.flows.merging.merging import GraphDifferenceNode, NodeConflictTypes, all_equal, group_by, has_result
from temba.flows.merging.serializers import (
    DiffNodeSerializer,
    GraphSerializer,
    NodeSerializer,
    deserialize_dict_param_from_request,
    deserialize_difference_graph,
    serialize_difference_graph,
)
from temba.tests import TembaTest


class TestMergingFlows(TembaTest):
    def setUp(self):
        super().setUp()
        self.flow1_json = {
            "nodes": [
                {
                    "exits": [
                        {
                            "destination_uuid": "bf69a5bf-7e2a-4a4a-965d-e67b8ee8086b",
                            "uuid": "627b3081-4dce-4867-aa64-43985bfce98e",
                        },
                        {
                            "uuid": "880080f2-0ec8-4c28-9505-c6f85fed72e4",
                            "destination_uuid": "25fc63ef-fadd-48f0-926d-44a0302110c3",
                        },
                        {
                            "uuid": "2570cb5b-084c-424b-b16c-bde2ab2e05f7",
                            "destination_uuid": "70b3ab7e-3ca1-4fa5-ad15-c3767ad25281",
                        },
                    ],
                    "router": {
                        "cases": [
                            {
                                "arguments": ["651145bb-8940-4dd8-8080-74a1285e5360", "Monkey Facts"],
                                "category_uuid": "6d7fd88c-1edc-4f72-a2d0-0d8dfcd4e965",
                                "type": "has_group",
                                "uuid": "64197de8-9b41-4467-8a76-ace089d8e8c6",
                            },
                            {
                                "arguments": ["d5d700b4-c232-4fee-901a-00df1d4143fc", "Fish Facts"],
                                "category_uuid": "12cd44e2-0c73-4a48-9da8-1cdc1e16b679",
                                "type": "has_group",
                                "uuid": "57a266d6-0c91-4243-962c-520161749378",
                            },
                        ],
                        "categories": [
                            {
                                "exit_uuid": "627b3081-4dce-4867-aa64-43985bfce98e",
                                "name": "Monkey Facts",
                                "uuid": "6d7fd88c-1edc-4f72-a2d0-0d8dfcd4e965",
                            },
                            {
                                "exit_uuid": "880080f2-0ec8-4c28-9505-c6f85fed72e4",
                                "name": "Fish Facts",
                                "uuid": "12cd44e2-0c73-4a48-9da8-1cdc1e16b679",
                            },
                            {
                                "exit_uuid": "2570cb5b-084c-424b-b16c-bde2ab2e05f7",
                                "name": "Other",
                                "uuid": "e63e7e4e-0a54-4cd3-a5b3-f17155533338",
                            },
                        ],
                        "default_category_uuid": "e63e7e4e-0a54-4cd3-a5b3-f17155533338",
                        "operand": "@contact.groups",
                        "result_name": "Group Split",
                        "type": "switch",
                    },
                    "uuid": "23a6a459-c166-4571-9235-917a9112a548",
                    "actions": [],
                },
                {
                    "exits": [{"uuid": "380a5dfe-6408-4393-af9d-eee667a6a53c"}],
                    "router": {
                        "cases": [],
                        "categories": [
                            {
                                "exit_uuid": "380a5dfe-6408-4393-af9d-eee667a6a53c",
                                "name": "All Responses",
                                "uuid": "fbb45f13-c54f-41f0-8137-d9c511c89888",
                            }
                        ],
                        "default_category_uuid": "fbb45f13-c54f-41f0-8137-d9c511c89888",
                        "operand": '@(if(is_error(fields.expression_split), "@contact.expression_split", fields.expression_split))',
                        "result_name": "Response 4",
                        "type": "switch",
                    },
                    "uuid": "bf69a5bf-7e2a-4a4a-965d-e67b8ee8086b",
                    "actions": [],
                },
                {
                    "uuid": "25fc63ef-fadd-48f0-926d-44a0302110c3",
                    "actions": [
                        {
                            "uuid": "7ebd7be9-18d4-4d06-b69e-63d11d7bb72e",
                            "type": "call_classifier",
                            "result_name": "_Result Classification",
                            "input": "@input.text",
                            "classifier": {"uuid": "891a1c5d-1140-4fd0-bd0d-a919ea25abb6", "name": "Feelings"},
                        }
                    ],
                    "router": {
                        "cases": [
                            {
                                "arguments": ["None", ".9"],
                                "type": "has_top_intent",
                                "uuid": "f7889a15-9d27-47e4-8132-011fd3e56473",
                                "category_uuid": "0a1fb250-2a5f-4995-81eb-0be34ee76f6d",
                            },
                            {
                                "uuid": "08cb55c0-9a89-41fb-a1d7-52f1aa65f5b9",
                                "type": "has_category",
                                "arguments": ["Success", "Skipped"],
                                "category_uuid": "d1c69a12-68d9-435a-80be-f1409b57c62f",
                            },
                        ],
                        "operand": "@results._result_classification",
                        "categories": [
                            {
                                "uuid": "0a1fb250-2a5f-4995-81eb-0be34ee76f6d",
                                "name": "None",
                                "exit_uuid": "89a4604e-d8bc-4836-8810-84ef86d7998c",
                            },
                            {
                                "uuid": "f113da14-35e1-4cd4-a0dc-c259f5e6f2e5",
                                "name": "Failure",
                                "exit_uuid": "509b4d09-c262-4fcb-a20d-ad2d431d1229",
                            },
                            {
                                "uuid": "d1c69a12-68d9-435a-80be-f1409b57c62f",
                                "name": "Other",
                                "exit_uuid": "4936d570-f4b4-407c-842f-59cf0b7bd54b",
                            },
                        ],
                        "type": "switch",
                        "default_category_uuid": "f113da14-35e1-4cd4-a0dc-c259f5e6f2e5",
                        "result_name": "Result",
                    },
                    "exits": [
                        {"uuid": "89a4604e-d8bc-4836-8810-84ef86d7998c"},
                        {"uuid": "4936d570-f4b4-407c-842f-59cf0b7bd54b"},
                        {"uuid": "509b4d09-c262-4fcb-a20d-ad2d431d1229"},
                    ],
                },
                {
                    "uuid": "70b3ab7e-3ca1-4fa5-ad15-c3767ad25281",
                    "actions": [
                        {
                            "uuid": "78f73b5c-b842-4b7c-ac74-54a2b1e31b79",
                            "type": "open_ticket",
                            "ticketer": {"uuid": "6ceb51cd-1d19-4f28-a9c3-2e244a9e2959", "name": "Zendesk"},
                            "subject": "@run.flow.name",
                            "body": "@results",
                            "result_name": "Result",
                        }
                    ],
                    "router": {
                        "type": "switch",
                        "operand": "@results.result",
                        "cases": [
                            {
                                "uuid": "e8af44bc-e5e0-46e7-816d-2eee0cf1928c",
                                "type": "has_category",
                                "arguments": ["Success"],
                                "category_uuid": "9ae9400a-eb5d-491a-bae8-ae295811c9d1",
                            }
                        ],
                        "categories": [
                            {
                                "uuid": "9ae9400a-eb5d-491a-bae8-ae295811c9d1",
                                "name": "Success",
                                "exit_uuid": "35d0d802-0d0f-418b-9028-5a0812761e97",
                            },
                            {
                                "uuid": "494df32e-1b36-4fd4-978a-0769c8b3b5ac",
                                "name": "Failure",
                                "exit_uuid": "2ec47a11-ee11-48b3-9207-3431fd354274",
                            },
                        ],
                        "default_category_uuid": "494df32e-1b36-4fd4-978a-0769c8b3b5ac",
                    },
                    "exits": [
                        {"uuid": "35d0d802-0d0f-418b-9028-5a0812761e97", "destination_uuid": None},
                        {"uuid": "2ec47a11-ee11-48b3-9207-3431fd354274", "destination_uuid": None},
                    ],
                },
            ]
        }

        self.sample_nodes = [
            {"actions": [{"type": "call_classifier"}]},
            {"actions": [{"type": "set_contact"}]},
            {"router": {"type": "random"}},
            {"router": {"wait": {"type": "msg"}}},
            {"router": {}, "actions": [{"type": "call_classifier"}]},
            {"router": {"operand": "@input.name"}},
            {"router": {"operand": "@input.result"}},
            {"router": {"operand": "@input.text"}},
            {"router": {"operand": "@input.groups"}},
            {"router": {"operand": "scheme"}},
            {"router": {}},
        ]

    def test_node(self):
        def copy(obj):
            return pickle.loads(pickle.dumps(obj))

        flow1 = self.get_flow("group_split_no_name")
        flow2 = self.get_flow("favorites_v13")

        flow_def = flow1.get_definition()
        flow_def2 = flow2.get_definition()

        node1 = Node(flow_def["nodes"][0]["uuid"])
        node3 = Node(flow_def2["nodes"][0]["uuid"])

        node1.data = flow_def["nodes"][0]
        node1.has_router = True
        node1.node_types = {"send_msg"}
        node2 = copy(node1)
        node2.uuid = "3d6c6132-8b84-4b0b-b945-8b4ae2ee8696"
        node3.data = flow_def2["nodes"][1]

        self.assertTrue(node1 == node2)
        self.assertFalse(node1 == node3)

        node2.has_router = False
        self.assertFalse(node1 == node2)
        node1.data["actions"] = [{"type": "enter_flow", "flow": {"uuid": "test-uuid"}, "result_name": ""}]
        node2 = copy(node1)
        self.assertTrue(node1 == node2)
        node1.data["actions"] = [{"type": "call_webhook", "result_name": ""}]
        node2 = copy(node1)
        self.assertTrue(node1 == node2)

        node1.data["actions"] = [{"type": "call_giftcard", "result_name": "Test"}]
        node2 = copy(node1)
        self.assertTrue(node1 == node2)

        node1_categories = node1.get_routing_categories()
        self.assertEqual(node1_categories, {"Approved": None, "Other": None})

        exits = [
            {
                "uuid": "d7a36118-0a38-4b35-a7e4-ae89042f0d3c",
                "destination_uuid": "3dcccbb4-d29c-41dd-a01f-16d814c9ab82",
            }
        ]
        router_replacement = {
            "type": "switch",
            "wait": {"type": "msg"},
            "categories": [
                {
                    "uuid": "37d8813f-1402-4ad2-9cc2-e9054a96525b",
                    "name": "All Responses",
                    "exit_uuid": "d7a36118-0a38-4b35-a7e4-ae89042f0d3c",
                }
            ],
            "operand": "@input.text",
            "default_category_uuid": "37d8813f-1402-4ad2-9cc2-e9054a96525b",
        }

        node2_categories = node2.get_routing_categories()
        self.assertEqual(node2_categories, {"Approved": None, "Other": None})

        node2.data["exits"] = exits
        node2.data["router"] = router_replacement
        self.assertEqual(node2_categories, {"Approved": None, "Other": None})
        node2.routing_categories = {}

        node2_categories = node2.get_routing_categories()
        self.assertEqual(node2_categories, {"All Responses": "3dcccbb4-d29c-41dd-a01f-16d814c9ab82"})

    def test_graph_class(self):
        graph1 = Graph(resource=self.flow1_json)

        self.assertEqual(len(graph1.result_names), 5)

        node_data1 = {"actions": []}
        node_data2 = {"actions": [{"result_name": "Color"}]}
        graph1.extract_result_names(node_data1)
        self.assertEqual(len(graph1.result_names), 5)
        graph1.extract_result_names(node_data2)
        self.assertEqual(len(graph1.result_names), 6)

        not_unique_results_name = graph1.get_not_unique_result_names()
        self.assertEqual(not_unique_results_name, ["Result"])

    def test_graph_difference_node(self):
        graph1 = Graph(resource=self.flow1_json)
        node_instances = list(graph1.nodes_map.values())
        main_node = node_instances[0]
        left_node = node_instances[1]
        right_node = node_instances[2]

        graph_node = GraphDifferenceNode(
            "3d6c6132-8b84-4b0b-b945-8b4ae2ee8696", left_node=left_node, right_node=right_node, graph=graph1
        )
        graph_node.data = main_node.data
        self.assertEqual(graph_node.data["uuid"], main_node.uuid)

        graph_node.correct_uuids()
        self.assertNotEqual(graph_node.data["uuid"], main_node.uuid)
        self.assertEqual(graph_node.data["uuid"], graph_node.uuid)

        self.assertEqual(len(graph_node.origin_exits_map), 0)
        graph_node.match_exits()
        self.assertEqual(len(graph_node.origin_exits_map), 2)

    def test_get_flow_step_name(self):
        flow_json = self.flow1_json

        flow_name = get_flow_step_name(flow_json["nodes"][0])
        self.assertEqual(flow_name, "Split by Group Membership")

        nodes = self.sample_nodes
        self.assertEqual(get_flow_step_name(nodes[0]), "")
        self.assertEqual(get_flow_step_name(nodes[1]), actions_names.get("set_contact_field").get("name", ""))
        self.assertEqual(get_flow_step_name(nodes[2]), actions_names.get("split_by_random").get("name", ""))
        self.assertEqual(get_flow_step_name(nodes[3]), actions_names.get("wait_for_response").get("name", ""))
        self.assertEqual(get_flow_step_name(nodes[4]), "")
        self.assertEqual(get_flow_step_name(nodes[5]), actions_names.get("split_by_name").get("name", ""))
        self.assertEqual(get_flow_step_name(nodes[6]), actions_names.get("split_by_run_result").get("name", ""))
        self.assertEqual(get_flow_step_name(nodes[7]), actions_names.get("split_by_expression").get("name", ""))
        self.assertEqual(get_flow_step_name(nodes[8]), actions_names.get("split_by_groups").get("name", ""))
        self.assertEqual(get_flow_step_name(nodes[9]), actions_names.get("split_by_scheme").get("name", ""))
        self.assertEqual(get_flow_step_name(nodes[10], "Test Flow"), "Test Flow")

    def test_get_flow_step_type(self):
        nodes = self.sample_nodes
        self.assertEqual(get_flow_step_type(nodes[0]), "call_classifier")
        self.assertEqual(get_flow_step_type(nodes[1]), "set_contact_field")
        self.assertEqual(get_flow_step_type(nodes[2]), "split_by_random")
        self.assertEqual(get_flow_step_type(nodes[3]), "wait_for_response")
        self.assertEqual(get_flow_step_type(nodes[4]), "call_classifier")
        self.assertEqual(get_flow_step_type(nodes[5]), "split_by_name")
        self.assertEqual(get_flow_step_type(nodes[6]), "split_by_run_result")
        self.assertEqual(get_flow_step_type(nodes[7]), "split_by_expression")
        self.assertEqual(get_flow_step_type(nodes[8]), "split_by_groups")
        self.assertEqual(get_flow_step_type(nodes[9]), "split_by_scheme")
        self.assertEqual(get_flow_step_type(nodes[10], "sample-name"), "sample-name")


class TestUtilityFunctions(TembaTest):
    def test_all_equal(self):
        self.assertTrue(all_equal([1, 1, 1]))
        self.assertTrue(all_equal([]))
        self.assertTrue(all_equal(["a"]))
        self.assertFalse(all_equal([1, 2, 1]))

    def test_has_result(self):
        # router result_name
        self.assertEqual(has_result({"router": {"result_name": "Color"}, "actions": []}), "Color")
        # action result_name
        self.assertEqual(has_result({"actions": [{"result_name": "Webhook"}]}), "Webhook")
        # no result
        self.assertFalse(has_result({"actions": [], "router": {}}))
        # empty
        self.assertFalse(has_result({"actions": []}))

    def test_group_by(self):
        items = [{"type": "a", "v": 1}, {"type": "b", "v": 2}, {"type": "a", "v": 3}]
        result = group_by(items, key=lambda x: x["type"])
        self.assertEqual(len(result["a"]), 2)
        self.assertEqual(len(result["b"]), 1)


class TestNodeEquality(TembaTest):
    def _make_node(self, uuid, data, node_types=None, has_router=False):
        node = Node(uuid)
        node.data = data
        node.node_types = node_types or set()
        node.has_router = has_router
        return node

    def test_node_str_repr_hash(self):
        node = Node("abc-123")
        node.node_types = {"send_msg"}
        self.assertIn("abc-123", str(node))
        self.assertEqual(repr(node), "abc-123")
        self.assertEqual(hash(node), hash("abc-123"))

    def test_node_eq_non_node(self):
        node = Node("abc")
        node.node_types = {"send_msg"}
        node.has_router = False
        node.data = {"actions": [], "router": {}}
        self.assertNotEqual(node, "not_a_node")

    def test_node_get_parent_children(self):
        parent = Node("parent")
        child = Node("child")
        child.set_parent(parent)
        self.assertEqual(child.get_parent(), "parent")
        self.assertEqual(parent.get_children(), ["child"])

        orphan = Node("orphan")
        self.assertIsNone(orphan.get_parent())
        self.assertEqual(orphan.get_children(), [])

    def test_node_set_parent_already_has_parent(self):
        parent1 = Node("p1")
        parent2 = Node("p2")
        child = Node("c")
        child.set_parent(parent1)
        child.set_parent(parent2)  # should not change parent, but add as child to parent2
        self.assertEqual(child.get_parent(), "p1")
        self.assertIn(child, parent2.children)

    def test_node_eq_send_msg_similarity(self):
        data1 = {"actions": [{"type": "send_msg", "text": "Hello World", "uuid": "a1"}]}
        data2 = {"actions": [{"type": "send_msg", "text": "Hello World!", "uuid": "a2"}]}
        data3 = {"actions": [{"type": "send_msg", "text": "Completely different", "uuid": "a3"}]}

        node1 = self._make_node("n1", data1, {"send_msg"})
        node2 = self._make_node("n2", data2, {"send_msg"})
        node3 = self._make_node("n3", data3, {"send_msg"})

        self.assertTrue(node1 == node2)  # similar text
        self.assertFalse(node1 == node3)  # different text

    def test_node_eq_set_contact_field(self):
        data1 = {"actions": [{"type": "set_contact_field", "field": {"key": "age"}, "uuid": "a1"}]}
        data2 = {"actions": [{"type": "set_contact_field", "field": {"key": "age"}, "uuid": "a2"}]}
        data3 = {"actions": [{"type": "set_contact_field", "field": {"key": "name"}, "uuid": "a3"}]}

        node1 = self._make_node("n1", data1, {"set_contact_field"})
        node2 = self._make_node("n2", data2, {"set_contact_field"})
        node3 = self._make_node("n3", data3, {"set_contact_field"})

        self.assertTrue(node1 == node2)
        self.assertFalse(node1 == node3)

    def test_node_eq_routers_different_types(self):
        data1 = {"actions": [], "router": {"type": "switch", "operand": "@input.text"}}
        data2 = {"actions": [], "router": {"type": "random"}}

        node1 = self._make_node("n1", data1, {"switch"}, has_router=True)
        node2 = self._make_node("n2", data2, {"switch", "random"}, has_router=True)

        self.assertFalse(node1 == node2)

    def test_node_eq_routers_different_result_names(self):
        data1 = {"actions": [], "router": {"type": "switch", "result_name": "r1", "operand": "@input.text"}}
        data2 = {"actions": [], "router": {"type": "switch", "result_name": "r2", "operand": "@input.text"}}

        node1 = self._make_node("n1", data1, {"switch"}, has_router=True)
        node2 = self._make_node("n2", data2, {"switch"}, has_router=True)

        self.assertFalse(node1 == node2)

    def test_node_eq_call_dialogflow(self):
        data1 = {
            "actions": [{"type": "call_dialogflow", "result_name": "df_result"}],
            "router": {"type": "switch", "result_name": "df_result"},
        }
        data2 = {
            "actions": [{"type": "call_dialogflow", "result_name": "df_result"}],
            "router": {"type": "switch", "result_name": "df_result"},
        }

        node1 = self._make_node("n1", data1, {"call_dialogflow", "switch"}, has_router=True)
        node2 = self._make_node("n2", data2, {"call_dialogflow", "switch"}, has_router=True)
        self.assertTrue(node1 == node2)

    def test_node_eq_call_lookup(self):
        data1 = {
            "actions": [{"type": "call_lookup", "result_name": "lookup_r"}],
            "router": {"type": "switch", "result_name": "lookup_r"},
        }
        data2 = {
            "actions": [{"type": "call_lookup", "result_name": "lookup_r"}],
            "router": {"type": "switch", "result_name": "lookup_r"},
        }

        node1 = self._make_node("n1", data1, {"call_lookup", "switch"}, has_router=True)
        node2 = self._make_node("n2", data2, {"call_lookup", "switch"}, has_router=True)
        self.assertTrue(node1 == node2)

    def test_node_eq_enter_flow(self):
        data1 = {
            "actions": [{"type": "enter_flow", "flow": {"uuid": "f1", "name": "Survey Flow"}}],
            "router": {"type": "switch", "result_name": "r1"},
        }
        data2 = {
            "actions": [{"type": "enter_flow", "flow": {"uuid": "f1", "name": "Survey Flow"}}],
            "router": {"type": "switch", "result_name": "r1"},
        }
        data3 = {
            "actions": [{"type": "enter_flow", "flow": {"uuid": "f2", "name": "Completely Diff"}}],
            "router": {"type": "switch", "result_name": "r1"},
        }

        node1 = self._make_node("n1", data1, {"enter_flow", "switch"}, has_router=True)
        node2 = self._make_node("n2", data2, {"enter_flow", "switch"}, has_router=True)
        node3 = self._make_node("n3", data3, {"enter_flow", "switch"}, has_router=True)
        self.assertTrue(node1 == node2)
        self.assertFalse(node1 == node3)

    def test_node_eq_switch_by_operand(self):
        data1 = {"actions": [], "router": {"type": "switch", "operand": "@input.text"}}
        data2 = {"actions": [], "router": {"type": "switch", "operand": "@input.text"}}
        data3 = {"actions": [], "router": {"type": "switch", "operand": "@contact.name"}}

        node1 = self._make_node("n1", data1, {"switch"}, has_router=True)
        node2 = self._make_node("n2", data2, {"switch"}, has_router=True)
        node3 = self._make_node("n3", data3, {"switch"}, has_router=True)

        self.assertTrue(node1 == node2)
        self.assertFalse(node1 == node3)

    def test_node_eq_router_action_count_mismatch(self):
        data1 = {"actions": [{"type": "send_msg", "text": "a"}], "router": {"type": "switch", "result_name": "r"}}
        data2 = {
            "actions": [{"type": "send_msg", "text": "a"}, {"type": "send_msg", "text": "b"}],
            "router": {"type": "switch", "result_name": "r"},
        }
        node1 = self._make_node("n1", data1, {"send_msg", "switch"}, has_router=True)
        node2 = self._make_node("n2", data2, {"send_msg", "switch"}, has_router=True)
        self.assertFalse(node1 == node2)


class TestGraphDifferenceMap(TembaTest):
    def _make_flow(self, nodes, ui_nodes=None):
        return {
            "name": "Test Flow",
            "uuid": "test-flow-uuid",
            "spec_version": "13.1.0",
            "language": "eng",
            "type": "messaging",
            "nodes": nodes,
            "_ui": {"nodes": ui_nodes or {}},
            "revision": 1,
            "expire_after_minutes": 10080,
        }

    def test_compare_identical_graphs(self):
        nodes = [
            {
                "uuid": "node-1",
                "actions": [{"type": "send_msg", "text": "Hello", "uuid": "a1"}],
                "exits": [{"uuid": "exit-1", "destination_uuid": None}],
            }
        ]
        flow1 = self._make_flow(nodes)
        flow2 = self._make_flow(copy.deepcopy(nodes))

        left = Graph(resource=flow1)
        right = Graph(resource=flow2)
        diff = GraphDifferenceMap(left, right)
        diff.compare_graphs()

        self.assertGreater(len(diff.diff_nodes_map), 0)
        self.assertIsNotNone(diff.definition)

    def test_compare_with_extra_node_in_right(self):
        nodes_left = [
            {
                "uuid": "node-1",
                "actions": [{"type": "send_msg", "text": "Hello", "uuid": "a1"}],
                "exits": [{"uuid": "exit-1", "destination_uuid": None}],
            }
        ]
        nodes_right = [
            {
                "uuid": "node-1",
                "actions": [{"type": "send_msg", "text": "Hello", "uuid": "a1"}],
                "exits": [{"uuid": "exit-1", "destination_uuid": "node-2"}],
            },
            {
                "uuid": "node-2",
                "actions": [{"type": "send_msg", "text": "Goodbye", "uuid": "a2"}],
                "exits": [{"uuid": "exit-2", "destination_uuid": None}],
            },
        ]
        left = Graph(resource=self._make_flow(nodes_left))
        right = Graph(resource=self._make_flow(nodes_right))
        diff = GraphDifferenceMap(left, right)
        diff.compare_graphs()

        # node-2 only exists in right, should still be in merged definition
        node_uuids = [n["uuid"] for n in diff.definition["nodes"]]
        self.assertIn("node-2", node_uuids)

    def test_compare_with_router_nodes(self):
        nodes = [
            {
                "uuid": "msg-node",
                "actions": [{"type": "send_msg", "text": "Pick a number", "uuid": "a1"}],
                "exits": [{"uuid": "e1", "destination_uuid": "router-node"}],
            },
            {
                "uuid": "router-node",
                "actions": [],
                "exits": [
                    {"uuid": "e2", "destination_uuid": None},
                    {"uuid": "e3", "destination_uuid": None},
                ],
                "router": {
                    "type": "switch",
                    "operand": "@input.text",
                    "result_name": "Choice",
                    "cases": [],
                    "categories": [
                        {"uuid": "cat1", "name": "Yes", "exit_uuid": "e2"},
                        {"uuid": "cat2", "name": "Other", "exit_uuid": "e3"},
                    ],
                    "default_category_uuid": "cat2",
                    "wait": {"type": "msg"},
                },
            },
        ]
        flow1 = self._make_flow(nodes)
        flow2 = self._make_flow(copy.deepcopy(nodes))

        left = Graph(resource=flow1)
        right = Graph(resource=flow2)
        diff = GraphDifferenceMap(left, right)
        diff.compare_graphs()

        self.assertIsNotNone(diff.definition)
        self.assertGreater(len(diff.diff_nodes_map), 0)

    def test_delete_unmatched_source_nodes(self):
        nodes_left = [
            {
                "uuid": "only-left",
                "actions": [{"type": "send_msg", "text": "Only in left", "uuid": "a1"}],
                "exits": [{"uuid": "e1", "destination_uuid": None}],
            }
        ]
        nodes_right = [
            {
                "uuid": "only-right",
                "actions": [{"type": "send_msg", "text": "Only in right", "uuid": "a2"}],
                "exits": [{"uuid": "e2", "destination_uuid": None}],
            }
        ]
        left = Graph(resource=self._make_flow(nodes_left))
        right = Graph(resource=self._make_flow(nodes_right))
        diff = GraphDifferenceMap(left, right)
        diff.compare_graphs()

        # left-only nodes should be removed
        self.assertNotIn("only-left", diff.diff_nodes_map)

    def test_graph_difference_node_get_definition(self):
        node = Node("src")
        node.data = {"uuid": "src", "actions": [{"type": "send_msg", "text": "Hi"}]}
        diff_node = GraphDifferenceNode("diff", left_node=None, right_node=node)
        data = diff_node.get_definition()
        self.assertEqual(data["uuid"], "src")

    def test_graph_difference_node_resolve_action_conflict(self):
        diff_node = GraphDifferenceNode("diff")
        diff_node.data = {"uuid": "diff", "actions": []}
        diff_node.conflicts = [
            {
                "conflict_type": NodeConflictTypes.ACTION_CONFLICT,
                "left_action": {"uuid": "act1", "text": "old text", "type": "send_msg"},
                "right_action": {"uuid": "act1", "text": "new text", "type": "send_msg"},
                "field": "text",
            }
        ]
        remaining = diff_node.resolve_conflict("act1", "text", "resolved text")
        self.assertEqual(len(remaining), 0)

    def test_graph_difference_node_resolve_router_conflict(self):
        diff_node = GraphDifferenceNode("diff")
        diff_node.data = {"uuid": "diff", "actions": [], "router": {"type": "switch", "operand": "@input.text"}}
        diff_node.origin_exits_map = {}
        diff_node.conflicts = [
            {
                "conflict_type": NodeConflictTypes.ROUTER_CONFLICT,
                "left_router": {"type": "switch", "operand": "@input.text"},
                "right_router": {"type": "random"},
                "field": "type",
            }
        ]
        remaining = diff_node.resolve_conflict("router", "type", "random")
        self.assertEqual(len(remaining), 0)
        self.assertEqual(diff_node.data["router"]["type"], "random")

    def test_graph_difference_node_resolve_nonexistent_conflict(self):
        diff_node = GraphDifferenceNode("diff")
        diff_node.data = {"uuid": "diff", "actions": []}
        diff_node.conflicts = []
        remaining = diff_node.resolve_conflict("nonexistent", "field", "value")
        self.assertEqual(len(remaining), 0)


class TestSerializers(TembaTest):
    def test_node_serializer_create(self):
        data = {
            "uuid": "node-1",
            "node_types": ["send_msg"],
            "has_router": False,
            "routing_categories": {},
            "parent_routind_data": {},
            "data": {"uuid": "node-1", "actions": []},
        }
        serializer = NodeSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        node = serializer.save()
        self.assertIsInstance(node, Node)
        self.assertEqual(node.uuid, "node-1")

    def test_diff_node_serializer_create(self):
        data = {
            "uuid": "diff-1",
            "node_types": ["send_msg"],
            "source_node": {
                "uuid": "src-1",
                "node_types": ["send_msg"],
                "has_router": False,
                "routing_categories": {},
                "parent_routind_data": {},
                "data": {},
            },
            "destination_node": None,
            "conflicts": [],
            "origin_exits_map": {},
            "data": {"uuid": "diff-1"},
        }
        serializer = DiffNodeSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        node = serializer.save()
        self.assertIsInstance(node, GraphDifferenceNode)
        self.assertEqual(node.uuid, "diff-1")

    def test_serialize_deserialize_difference_graph(self):
        nodes = [
            {
                "uuid": "n1",
                "actions": [{"type": "send_msg", "text": "Hello", "uuid": "a1"}],
                "exits": [{"uuid": "e1", "destination_uuid": None}],
            }
        ]
        flow_def = {
            "name": "Test",
            "uuid": "f1",
            "spec_version": "13.1.0",
            "language": "eng",
            "type": "messaging",
            "nodes": nodes,
            "_ui": {"nodes": {}},
            "revision": 1,
            "expire_after_minutes": 10080,
        }
        left = Graph(resource=flow_def)
        right = Graph(resource=copy.deepcopy(flow_def))
        diff = GraphDifferenceMap(left, right)
        diff.compare_graphs()

        serialized = serialize_difference_graph(diff)
        self.assertIsInstance(serialized, dict)
        self.assertIn("left_graph", serialized)
        self.assertIn("diff_nodes_map", serialized)

        # test dumps=True
        serialized_str = serialize_difference_graph(diff, dumps=True)
        self.assertIsInstance(serialized_str, str)

        # deserialize back
        restored = deserialize_difference_graph(serialized)
        self.assertIsNotNone(restored)
        self.assertIsInstance(restored, GraphDifferenceMap)

        # deserialize from string
        restored2 = deserialize_difference_graph(serialized_str, loads=True)
        self.assertIsNotNone(restored2)

    def test_deserialize_dict_param_from_request(self):
        request_data = {
            "conflicts[abc-123_act1_text]": "new value",
            "conflicts[def-456_router_type]": "switch",
            "other_field": "ignored",
        }
        result = deserialize_dict_param_from_request("conflicts", request_data)
        self.assertEqual(result["abc-123_act1_text"], "new value")
        self.assertEqual(result["def-456_router_type"], "switch")
        self.assertNotIn("other_field", result)

    def test_graph_serializer_create(self):
        nodes = [
            {
                "uuid": "n1",
                "actions": [{"type": "send_msg", "text": "Hi", "uuid": "a1"}],
                "exits": [{"uuid": "e1", "destination_uuid": None}],
            }
        ]
        flow_def = {
            "name": "Test",
            "uuid": "f1",
            "spec_version": "13.1.0",
            "language": "eng",
            "type": "messaging",
            "nodes": nodes,
            "_ui": {},
            "revision": 1,
            "expire_after_minutes": 10080,
        }
        graph = Graph(resource=flow_def)
        serialized = GraphSerializer(instance=graph).data

        serializer = GraphSerializer(data=serialized)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        restored_graph = serializer.save()
        self.assertIsInstance(restored_graph, Graph)
        self.assertIn("n1", restored_graph.nodes_map)


class TestNodeEqReturnFalse(TembaTest):
    """Tests for Node.__eq__ branches that return False for mismatched action types."""

    def _make_router_node(self, uuid, action_type, result_name):
        node = Node(uuid)
        node.data = {
            "actions": [{"type": action_type, "result_name": result_name}],
            "router": {"type": "switch", "result_name": result_name},
        }
        node.node_types = {action_type, "switch"}
        node.has_router = True
        return node

    def test_call_webhook_mismatch(self):
        n1 = self._make_router_node("n1", "call_webhook", "r1")
        n2 = self._make_router_node("n2", "call_webhook", "r2")
        n2.data["router"]["result_name"] = "r1"  # router matches but action result differs
        self.assertFalse(n1 == n2)

    def test_call_giftcard_mismatch(self):
        n1 = self._make_router_node("n1", "call_giftcard", "g1")
        n2 = self._make_router_node("n2", "call_giftcard", "g2")
        n2.data["router"]["result_name"] = "g1"
        self.assertFalse(n1 == n2)

    def test_call_dialogflow_mismatch(self):
        n1 = self._make_router_node("n1", "call_dialogflow", "d1")
        n2 = self._make_router_node("n2", "call_dialogflow", "d2")
        n2.data["router"]["result_name"] = "d1"
        self.assertFalse(n1 == n2)

    def test_call_lookup_mismatch(self):
        n1 = self._make_router_node("n1", "call_lookup", "l1")
        n2 = self._make_router_node("n2", "call_lookup", "l2")
        n2.data["router"]["result_name"] = "l1"
        self.assertFalse(n1 == n2)

    def test_router_action_type_mismatch(self):
        """Line 85: actions present but first action types differ."""
        n1 = Node("n1")
        n1.data = {"actions": [{"type": "send_msg", "text": "hi"}], "router": {"type": "switch", "result_name": "r"}}
        n1.node_types = {"send_msg", "switch"}
        n1.has_router = True

        n2 = Node("n2")
        n2.data = {
            "actions": [{"type": "enter_flow", "flow": {"uuid": "f1", "name": "F"}}],
            "router": {"type": "switch", "result_name": "r"},
        }
        n2.node_types = {"send_msg", "switch"}
        n2.has_router = True

        self.assertFalse(n1 == n2)


class TestGraphDifferenceMapAdvanced(TembaTest):
    def _make_flow(self, nodes, ui_nodes=None):
        return {
            "name": "Test Flow",
            "uuid": "test-flow-uuid",
            "spec_version": "13.1.0",
            "language": "eng",
            "type": "messaging",
            "nodes": nodes,
            "_ui": {"nodes": ui_nodes or {}},
            "revision": 1,
            "expire_after_minutes": 10080,
        }

    def test_get_conflict_solutions(self):
        """Lines 536-582."""
        nodes = [
            {
                "uuid": "n1",
                "actions": [{"type": "send_msg", "text": "Hello", "uuid": "a1"}],
                "exits": [{"uuid": "e1", "destination_uuid": None}],
            }
        ]
        left = Graph(resource=self._make_flow(nodes))
        right = Graph(resource=self._make_flow(copy.deepcopy(nodes)))
        diff = GraphDifferenceMap(left, right)
        diff.compare_graphs()

        # inject action conflict
        diff.conflicts["n1"] = [
            {
                "conflict_type": NodeConflictTypes.ACTION_CONFLICT,
                "left_action": {"uuid": "a1", "text": "Hello", "type": "send_msg"},
                "right_action": {"uuid": "a1", "text": "Hi", "type": "send_msg"},
                "field": "text",
            }
        ]
        solutions = diff.get_conflict_solutions()
        self.assertEqual(len(solutions), 1)
        self.assertIn("Hello", solutions[0]["solutions"])
        self.assertIn("Hi", solutions[0]["solutions"])

    def test_get_conflict_solutions_router(self):
        nodes = [
            {
                "uuid": "n1",
                "actions": [],
                "exits": [{"uuid": "e1", "destination_uuid": None}],
                "router": {
                    "type": "switch",
                    "operand": "@input.text",
                    "result_name": "r",
                    "categories": [],
                    "cases": [],
                    "default_category_uuid": "c1",
                },
            }
        ]
        left = Graph(resource=self._make_flow(nodes))
        right = Graph(resource=self._make_flow(copy.deepcopy(nodes)))
        diff = GraphDifferenceMap(left, right)
        diff.compare_graphs()

        diff.conflicts["n1"] = [
            {
                "conflict_type": NodeConflictTypes.ROUTER_CONFLICT,
                "left_router": {"type": "switch", "operand": "@input.text"},
                "right_router": {"type": "switch", "operand": "@contact.name"},
                "field": "operand",
            }
        ]
        solutions = diff.get_conflict_solutions()
        self.assertEqual(len(solutions), 1)
        self.assertIn("@input.text", solutions[0]["solutions"])

    def test_apply_conflict_resolving(self):
        """Lines 585-593."""
        nodes = [
            {
                "uuid": "n1",
                "actions": [{"type": "send_msg", "text": "Hello", "uuid": "a1"}],
                "exits": [{"uuid": "e1", "destination_uuid": None}],
            }
        ]
        left = Graph(resource=self._make_flow(nodes))
        right = Graph(resource=self._make_flow(copy.deepcopy(nodes)))
        diff = GraphDifferenceMap(left, right)
        diff.compare_graphs()

        diff.conflicts["n1"] = [
            {
                "conflict_type": NodeConflictTypes.ACTION_CONFLICT,
                "left_action": {"uuid": "a1", "text": "Hello", "type": "send_msg"},
                "right_action": {"uuid": "a1", "text": "Hi", "type": "send_msg"},
                "field": "text",
            }
        ]
        diff.apply_conflict_resolving({"n1_a1_text": "Resolved"})
        self.assertNotIn("n1", diff.conflicts)

    def test_resolve_conflict_with_flow_field_json(self):
        """Lines 301-307: resolve_conflict with flow/channel/field that parses JSON."""
        diff_node = GraphDifferenceNode("diff")
        diff_node.data = {"uuid": "diff", "actions": []}
        diff_node.conflicts = [
            {
                "conflict_type": NodeConflictTypes.ACTION_CONFLICT,
                "left_action": {"uuid": "a1", "flow": {"uuid": "old", "name": "Old"}, "type": "enter_flow"},
                "right_action": {"uuid": "a1", "flow": {"uuid": "new", "name": "New"}, "type": "enter_flow"},
                "field": "flow",
            }
        ]
        diff_node.resolve_conflict("a1", "flow", '{"uuid": "new", "name": "New"}')
        action = diff_node.data["actions"][0]
        self.assertEqual(action["flow"]["uuid"], "new")

    def test_resolve_router_conflict_non_type_field(self):
        """Line 318: router conflict with non-type field."""
        diff_node = GraphDifferenceNode("diff")
        diff_node.data = {"uuid": "diff", "actions": [], "router": {"type": "switch", "operand": "@input.text"}}
        diff_node.conflicts = [
            {
                "conflict_type": NodeConflictTypes.ROUTER_CONFLICT,
                "left_router": {"operand": "@input.text"},
                "right_router": {"operand": "@contact.name"},
                "field": "operand",
            }
        ]
        diff_node.resolve_conflict("router", "operand", "@contact.name")
        self.assertEqual(diff_node.data["router"]["operand"], "@contact.name")

    def test_compare_with_multiple_matching_nodes(self):
        """Exercise flow_step_matching with multiple matches to trigger best_matches_tab logic."""
        nodes_left = [
            {
                "uuid": "left-1",
                "actions": [{"type": "send_msg", "text": "Hello there friend", "uuid": "a1"}],
                "exits": [{"uuid": "e1", "destination_uuid": None}],
            },
            {
                "uuid": "left-2",
                "actions": [{"type": "send_msg", "text": "Hello there buddy", "uuid": "a2"}],
                "exits": [{"uuid": "e2", "destination_uuid": None}],
            },
        ]
        nodes_right = [
            {
                "uuid": "right-1",
                "actions": [{"type": "send_msg", "text": "Hello there friend", "uuid": "a3"}],
                "exits": [{"uuid": "e3", "destination_uuid": None}],
            },
            {
                "uuid": "right-2",
                "actions": [{"type": "send_msg", "text": "Hello there buddy", "uuid": "a4"}],
                "exits": [{"uuid": "e4", "destination_uuid": None}],
            },
        ]
        ui_left = {
            "left-1": {"position": {"top": 0, "left": 0}},
            "left-2": {"position": {"top": 100, "left": 100}},
        }
        ui_right = {
            "right-1": {"position": {"top": 0, "left": 0}},
            "right-2": {"position": {"top": 100, "left": 100}},
        }
        left = Graph(resource=self._make_flow(nodes_left, ui_left))
        right = Graph(resource=self._make_flow(nodes_right, ui_right))
        diff = GraphDifferenceMap(left, right)
        diff.compare_graphs()

        self.assertGreater(len(diff.diff_nodes_map), 0)

    def test_node_get_routing_categories_no_router(self):
        """Line 160: routing categories when not a router."""
        node = Node("n")
        node.has_router = False
        node.routing_categories = {}
        result = node.get_routing_categories()
        self.assertEqual(result, {})

    def test_graph_difference_node_str(self):
        """Line 259."""
        dn = GraphDifferenceNode("diff-uuid")
        self.assertEqual(str(dn), "diff-uuid")

    def test_graph_difference_node_set_parent_already_has_parent(self):
        """Lines 264-266."""
        p1 = GraphDifferenceNode("p1")
        p2 = GraphDifferenceNode("p2")
        child = GraphDifferenceNode("c")
        child.parent = p1
        child.set_parent(p2)
        self.assertEqual(child.parent.uuid, "p1")  # unchanged
        self.assertIn(child, p2.children)

    def test_find_matching_children(self):
        """Lines 520-527."""
        nodes = [
            {
                "uuid": "parent-l",
                "actions": [{"type": "send_msg", "text": "Hello", "uuid": "a1"}],
                "exits": [{"uuid": "e1", "destination_uuid": "child-l"}],
            },
            {
                "uuid": "child-l",
                "actions": [{"type": "send_msg", "text": "World", "uuid": "a2"}],
                "exits": [{"uuid": "e2", "destination_uuid": None}],
            },
        ]
        left = Graph(resource=self._make_flow(nodes))
        right = Graph(resource=self._make_flow(copy.deepcopy(nodes)))
        diff = GraphDifferenceMap(left, right)
        diff.calculate_max_distance()

        parent_l = list(left.nodes_map.values())[0]
        parent_r = list(right.nodes_map.values())[0]
        pairs, has_children = diff.find_matching_children(parent_l, parent_r)
        self.assertTrue(has_children)
        self.assertGreater(len(pairs), 0)

    def test_calculate_max_distance_with_ui(self):
        """Lines 632-643."""
        ui = {
            "n1": {"position": {"top": 100, "left": 200}},
            "n2": {"position": {"top": 300, "left": 400}},
        }
        nodes = [
            {
                "uuid": "n1",
                "actions": [{"type": "send_msg", "text": "Hi", "uuid": "a1"}],
                "exits": [{"uuid": "e1", "destination_uuid": None}],
            }
        ]
        left = Graph(resource=self._make_flow(nodes, ui))
        right = Graph(resource=self._make_flow(copy.deepcopy(nodes), ui))
        diff = GraphDifferenceMap(left, right)
        diff.calculate_max_distance()
        self.assertGreater(diff.max_distance, 0)
