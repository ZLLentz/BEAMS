from dataclasses import dataclass, field, fields
from typing import Any, List, Optional, Union

from epics import caget
import py_trees
from apischema import deserialize, serialize
from py_trees.behaviour import Behaviour

from beams.serialization import as_tagged_union


@as_tagged_union
@dataclass
class BaseItem:
    name: str = ""
    description: str = ""

    def get_tree(self) -> Behaviour:
        """Get the tree node that this dataclass represents"""
        raise NotImplementedError


@dataclass
class BehaviorTreeItem:
    root: BaseItem

    def get_tree(self) -> py_trees.trees.BehaviourTree:
        return py_trees.trees.BehaviourTree(self.root.get_tree())


@dataclass
class ExternalItem(BaseItem):
    path: str = ""

    def get_tree(self) -> Behaviour:
        # grab file
        # de-serialize tree, return it
        raise NotImplementedError


@as_tagged_union
@dataclass
class Target:
    def get_value(self) -> Any:
        raise NotImplementedError
    
    # def get_signal(self) -> ophyd.Signal:
    #     pass

 
@dataclass
class ValueTarget(Target):
    value: Any

    def get_value(self) -> Any:
        return self.value


@dataclass
class PVTarget(Target):
    pv_name: str
    as_string: bool = False

    def get_value(self) -> Any:
        return caget(self.pv_name)
