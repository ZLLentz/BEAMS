from __future__ import annotations

import json
import logging
import operator
import time
from copy import copy
from dataclasses import dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any, Callable, List, Optional, Union

import py_trees
from apischema import deserialize, serialize
from epics import caget, caput
from py_trees.behaviour import Behaviour
from py_trees.behaviours import (CheckBlackboardVariableValue,
                                 WaitForBlackboardVariableValue)
from py_trees.common import ComparisonExpression, ParallelPolicy, Status
from py_trees.composites import Parallel, Selector, Sequence

from beams.behavior_tree.ActionNode import ActionNode
from beams.behavior_tree.CheckAndDo import CheckAndDo
from beams.behavior_tree.ConditionNode import ConditionNode
from beams.serialization import as_tagged_union

logger = logging.getLogger(__name__)


def get_tree_from_path(path: Union[Path, str]) -> py_trees.trees.BehaviourTree:
    """Deserialize a json file, return the tree it specifies"""
    with open(path, "r") as fd:
        deser = json.load(fd)
        tree_item = deserialize(BehaviorTreeItem, deser)

    return tree_item.get_tree()


def save_tree_to_path(path: Union[Path, str], root: BaseItem):
    """Serialize a behavior tree node to a json file."""
    ser = serialize(BehaviorTreeItem(root=root))

    with open(path, "w") as fd:
        json.dump(ser, fd, indent=2)
        fd.write("\n")


@dataclass
class BehaviorTreeItem:
    root: BaseItem

    def get_tree(self) -> py_trees.trees.BehaviourTree:
        return py_trees.trees.BehaviourTree(self.root.get_tree())


@as_tagged_union
@dataclass
class BaseItem:
    name: str = ""
    description: str = ""

    def get_tree(self) -> Behaviour:
        """Get the tree node that this dataclass represents"""
        raise NotImplementedError


@dataclass
class ExternalItem(BaseItem):
    path: str = ""

    def get_tree(self) -> Behaviour:
        # grab file
        # de-serialize tree, return it
        raise NotImplementedError


class ParallelMode(Enum):
    """Simple enum mimicing the ``py_trees.common.ParallelPolicy`` options"""

    Base = "Base"
    SuccessOnAll = "SuccesOnAll"
    SuccessOnONe = "SuccessOnOne"
    SuccessOnSelected = "SuccessOnSelected"


@dataclass
class ParallelItem(BaseItem):
    policy: ParallelMode = ParallelMode.Base
    children: List[BaseItem] = field(default_factory=list)

    def get_tree(self) -> Parallel:
        children = []
        for child in self.children:
            children.append(child.get_tree())

        node = Parallel(
            name=self.name,
            policy=getattr(ParallelPolicy, self.policy.value),
            children=children,
        )

        return node


@dataclass
class SelectorItem(BaseItem):
    """aka fallback node"""
    memory: bool = False
    children: List[BaseItem] = field(default_factory=list)

    def get_tree(self) -> Selector:
        children = []
        for child in self.children:
            children.append(child.get_tree())

        node = Selector(name=self.name, memory=self.memory, children=children)
        return node


def get_sequence_tree(seq_item: AnySequenceItem):
    children = []
    for child in seq_item.children:
        children.append(child.get_tree())

    node = Sequence(name=seq_item.name, memory=seq_item.memory, children=children)

    return node


@dataclass
class SequenceItem(BaseItem):
    memory: bool = False
    children: List[BaseItem] = field(default_factory=list)

    def get_tree(self) -> Sequence:
        return get_sequence_tree(self)


# Custom LCLS-built Behaviors (idioms)
class ConditionOperator(Enum):
    equal = "eq"
    not_equal = "ne"
    less = "lt"
    greater = "gt"
    less_equal = "le"
    greater_equal = "ge"


@dataclass
class ConditionItem(BaseItem):
    pv: str = ""
    value: Any = 1
    operator: ConditionOperator = ConditionOperator.equal

    def get_tree(self) -> ConditionNode:
        cond_func = self.get_condition_function()
        return ConditionNode(self.name, cond_func)

    def get_condition_function(self) -> Callable[[], bool]:
        op = getattr(operator, self.operator.value)

        def cond_func():
            val = caget(self.pv)
            if val is None:
                return False

            return op(val, self.value)

        return cond_func


@dataclass
class SequenceConditionItem(BaseItem):
    """
    A sequence containing only condition items.

    Suitable for use as an action item's termination_check.

    The condition function evaluates to "True" if every child's condition item
    also evaluates to "True".

    When not used as a termination_check, this behaves exactly
    like a normal Sequence Item.
    """
    memory: bool = False
    children: List[AnyConditionItem] = field(default_factory=list)

    def get_tree(self) -> Sequence:
        return get_sequence_tree(self)

    def get_condition_function(self) -> Callable[[], bool]:
        child_funcs = [item.get_condition_function() for item in self.children]

        def cond_func():
            """
            Minimize network hits by failing at first issue
            """
            ok = True
            for cf in child_funcs:
                ok = ok and cf()
                if not ok:
                    break
            return ok

        return cond_func


@dataclass
class RangeConditionItem(BaseItem):
    """
    Shorthand for a sequence of two condition items, establishing a range.
    """
    memory: bool = False
    pv: str = ""
    low_value: Any = 0
    high_value: Any = 1,

    def _generate_subconfig(self) -> SequenceConditionItem:
        low = ConditionItem(
            name=f"{self.name}_lower_bound",
            description=f"Lower bound for {self.name} check",
            pv=self.pv,
            value=self.low_value,
            operator=ConditionOperator.greater_equal,
        )
        high = ConditionItem(
            name=f"{self.name}_upper_bound",
            description=f"Upper bound for {self.name} check",
            pv=self.pv,
            value=self.high_value,
            operator=ConditionOperator.less_equal,
        )
        range = SequenceConditionItem(
            name=self.name,
            description=self.description,
            memory=self.memory,
            children=[low, high],
        )
        return range

    def get_tree(self) -> Sequence:
        return self._generate_subconfig().get_tree()

    def get_condition_function(self) -> Callable[[], bool]:
        return self._generate_subconfig().get_condition_function()


@dataclass
class SetPVActionItem(BaseItem):
    pv: str = ""
    value: Any = 1
    loop_period_sec: float = 1.0

    termination_check: AnyConditionItem = field(default_factory=ConditionItem)

    def get_tree(self) -> ActionNode:

        def work_func(comp_condition: Callable[[], bool]):
            try:
                # Set to running
                value = caget(self.termination_check.pv)

                if comp_condition():
                    return py_trees.common.Status.SUCCESS
                logger.debug(f"{self.name}: Value is {value}")

                # specific caput logic to SetPVActionItem
                caput(self.pv, self.value)
                time.sleep(self.loop_period_sec)
                return py_trees.common.Status.RUNNING
            except Exception as ex:
                logger.warning(f"{self.name}: work failed: {ex}")
                return py_trees.common.Status.FAILURE

        comp_cond = self.termination_check.get_condition_function()

        node = ActionNode(
            name=self.name,
            work_func=work_func,
            completion_condition=comp_cond,
        )

        return node


@dataclass
class IncPVActionItem(BaseItem):
    pv: str = ""
    increment: float = 1
    loop_period_sec: float = 1.0

    termination_check: AnyConditionItem = field(default_factory=ConditionItem)

    def get_tree(self) -> ActionNode:

        def work_func(comp_condition: Callable[[], bool]) -> py_trees.common.Status:
            """
            To be run inside of a while loop
            Action node should take care of logging, reporting status
            """
            try:
                value = caget(self.pv)

                logging.debug(f"(wf) {self.name}: Value is {value}")
                if comp_condition():
                    return py_trees.common.Status.SUCCESS

                # specific caput logic to IncPVActionItem
                caput(self.pv, value + self.increment)
                time.sleep(self.loop_period_sec)
                return py_trees.common.Status.RUNNING
            except Exception as ex:
                logger.warning(f"{self.name}: work failed: {ex}")
                return py_trees.common.Status.FAILURE

        comp_cond = self.termination_check.get_condition_function()

        node = ActionNode(
            name=self.name,
            work_func=work_func,
            completion_condition=comp_cond,
        )

        return node


@dataclass
class CheckAndDoItem(BaseItem):
    check: AnyConditionItem = field(default_factory=ConditionItem)
    do: Union[SetPVActionItem, IncPVActionItem] = field(default_factory=SetPVActionItem)

    def __post_init__(self):
        # Clearly indicate the intent for serialization
        # If no termination check, use the check's check
        if not self.do.termination_check.name:
            self.do.termination_check = UseCheckConditionItem()

    def get_tree(self) -> CheckAndDo:
        if isinstance(self.do.termination_check, UseCheckConditionItem):
            active_do = copy(self.do)
            active_do.termination_check = self.check
        else:
            active_do = self.do

        check_node = self.check.get_tree()
        do_node = active_do.get_tree()

        node = CheckAndDo(name=self.name, check=check_node, do=do_node)

        return node


@dataclass
class UseCheckConditionItem(BaseItem):
    """
    Dummy item: indicates that check and do should use "check" as do's termination check.
    """
    copy_from: str = "previous check"


# py_trees.behaviours Behaviour items
class PyTreesItem:
    def get_tree(self):
        cls = getattr(py_trees.behaviours, type(self).__name__.removesuffix('Item'))
        kwargs = {}
        for inst_field in fields(self):
            if inst_field.name in ('description',):
                continue
            kwargs[inst_field.name] = getattr(self, inst_field.name)

        return cls(**kwargs)


@dataclass
class SuccessItem(PyTreesItem, BaseItem):
    pass


@dataclass
class FailureItem(PyTreesItem, BaseItem):
    pass


@dataclass
class RunningItem(PyTreesItem, BaseItem):
    pass


@dataclass
class DummyItem(PyTreesItem, BaseItem):
    pass


@dataclass
class PeriodicItem(PyTreesItem, BaseItem):
    n: int = 1


@dataclass
class StatusQueueItem(PyTreesItem, BaseItem):
    queue: list[Status] = field(default_factory=list)
    eventually: Optional[Status] = None


@dataclass
class SuccessEveryNItem(PyTreesItem, BaseItem):
    n: int = 2


@dataclass
class TickCounterItem(PyTreesItem, BaseItem):
    duration: int = 5
    completion_status: Status = Status.SUCCESS


@dataclass
class BlackboardToStatusItem(PyTreesItem, BaseItem):
    variable_name: str = 'default_variable'


@dataclass
class CheckBlackboardVariableExistsItem(PyTreesItem, BaseItem):
    variable_name: str = 'default_variable'


@dataclass
class WaitForBlackboardVariableItem(PyTreesItem, BaseItem):
    variable_name: str = 'default_variable'


@dataclass
class UnsetBlackboardVariableItem(PyTreesItem, BaseItem):
    key: str = 'default_variable'


@dataclass
class SetBlackboardVariableItem(PyTreesItem, BaseItem):
    variable_name: str = 'default_variable'
    variable_value: Any = 1
    overwrite: bool = True


@dataclass
class PyTreesComparison:
    variable_name: str = ''
    value: Any = 1
    operator: ConditionOperator = ConditionOperator.equal


@dataclass
class CheckBlackboardVariableValueItem(BaseItem):
    check: PyTreesComparison = field(default_factory=PyTreesComparison)

    def get_tree(self):
        comp_exp = ComparisonExpression(
            variable=self.check.variable_name,
            value=self.check.value,
            operator=getattr(operator, self.check.operator.value)
        )
        return CheckBlackboardVariableValue(name=self.name, check=comp_exp)


@dataclass
class WaitForBlackboardVariableValueItem(BaseItem):
    check: PyTreesComparison = field(default_factory=PyTreesComparison)

    def get_tree(self):
        comp_exp = ComparisonExpression(
            variable=self.check.variable_name,
            value=self.check.value,
            operator=getattr(operator, self.check.operator.value)
        )
        return WaitForBlackboardVariableValue(name=self.name, check=comp_exp)


AnyConditionItem = Union[ConditionItem, SequenceConditionItem, RangeConditionItem, UseCheckConditionItem]
AnySequenceItem = Union[SequenceItem, SequenceConditionItem]
