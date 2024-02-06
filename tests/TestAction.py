import py_trees
from beams.ActionNode import ActionNode
import time
from multiprocessing import Value


class TestTask:
  def test_task_state_cycle(self, capsys):
    py_trees.logging.level = py_trees.logging.Level.DEBUG
    # For test
    percentage_complete = Value('i', 0)

    def thisjob(comp_condition, volatile_status, **kwargs) -> None:
      try:
        # grabbing intended keyword argument. Josh's less than pythonic mechanism for closures
        percentage_complete = kwargs["percentage_complete"]
        while not comp_condition(percentage_complete.value):
          py_trees.console.logdebug(f"yuh {percentage_complete.value}, {volatile_status.get_value()}")
          percentage_complete.value += 10
          if percentage_complete.value == 100:
            volatile_status.set_value(py_trees.common.Status.SUCCESS)
          time.sleep(0.001)
      except KeyboardInterrupt:
        pass

    py_trees.logging.level = py_trees.logging.Level.DEBUG
    comp_cond = lambda x: x == 100
    action = ActionNode("action", thisjob, comp_cond, percentage_complete=percentage_complete)
    action.setup()
    for i in range(20):
      action.tick_once()
      time.sleep(0.01)

    assert percentage_complete.value == 100

  def test_two(self):
    # While I have no love for meaningless commited code
    # this serves as a reminder to write more tests!!
    self.value = 2
    assert self.value == 2