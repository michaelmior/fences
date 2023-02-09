from unittest import TestCase
from fences.core.exception import GraphException
from fences.core.node import NoOpDecision, NoOpLeaf, IncomingTransition, OutgoingTransition
from fences.core.debug import check

class CheckTest(TestCase):

    def test_invalid_outgoing_idx(self):
        child = NoOpLeaf(None, True)
        root = NoOpDecision(None)
        child.incoming_transitions.append( IncomingTransition(root, 1) )
        root.outgoing_transitions.append( OutgoingTransition(child) )
        with self.assertRaises(GraphException):
            check(child)
        child.incoming_transitions[0].outgoing_idx = 0
        check(child)

    def test_source_not_decision(self):
        dummy = NoOpLeaf(None, True)
        child = NoOpLeaf(None, True)
        root = NoOpDecision(None)
        child.incoming_transitions.append( IncomingTransition(dummy, 0) )
        root.outgoing_transitions.append( OutgoingTransition(child) )
        with self.assertRaises(GraphException):
            check(child)
        child.incoming_transitions[0].source = root
        check(child)

    def test_invalid_target(self):
        dummy = NoOpDecision(None)
        child = NoOpLeaf(None, True)
        root = NoOpDecision(None)
        child.incoming_transitions.append( IncomingTransition(root, 0) )
        root.outgoing_transitions.append( OutgoingTransition(dummy) )
        with self.assertRaises(GraphException):
            check(child)
        root.outgoing_transitions[0].target = child
        check(child)

    def test_invalid_index(self):
        child = NoOpLeaf(None, True)
        root = NoOpDecision(None)
        child.incoming_transitions.append( IncomingTransition(root, 1) )
        root.outgoing_transitions.append( OutgoingTransition(child) )
        with self.assertRaises(GraphException):
            check(root)
        child.incoming_transitions[0].outgoing_idx = 0
        check(root)

    def test_no_matching_incoming(self):
        child = NoOpLeaf(None, True)
        root = NoOpDecision(None)
        root.outgoing_transitions.append( OutgoingTransition(child) )
        with self.assertRaises(GraphException):
            check(root)
        child.incoming_transitions.append( IncomingTransition(root, 0) )
        check(root)