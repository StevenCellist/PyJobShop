import numpy as np
from ortools.sat.python.cp_model import CpModel, LinearExpr

import pyjobshop.solvers.utils as utils
from pyjobshop.constants import MAX_VALUE
from pyjobshop.ProblemData import (
    Machine,
    NonRenewable,
    ProblemData,
    Renewable,
)
from pyjobshop.solvers.ortools.Variables import Variables


class Constraints:
    """
    Builds the core constraints of the OR-Tools model.
    """

    def __init__(
        self, model: CpModel, data: ProblemData, variables: Variables
    ):
        self._model = model
        self._data = data
        self._job_vars = variables.job_vars
        self._task_vars = variables.task_vars
        self._mode_vars = variables.mode_vars
        self._sequence_vars = variables.sequence_vars
        self._flow_vars = variables.flow_vars

    def _job_spans_tasks(self):
        """
        Ensures that the job variables span the related task variables.
        """
        model, data = self._model, self._data

        for idx, job in enumerate(data.jobs):
            job_var = self._job_vars[idx]
            task_starts = []
            task_ends = []

            for task in job.tasks:
                task_var = self._task_vars[task]

                if data.tasks[task].optional:
                    # When tasks are absent, they should not restrict the job's
                    # start and end times.
                    task_start = model.new_int_var(0, MAX_VALUE, "")
                    task_end = model.new_int_var(0, MAX_VALUE, "")

                    expr = task_start == task_var.start
                    model.add(expr).only_enforce_if(task_var.present)

                    expr = task_end == task_var.end
                    model.add(expr).only_enforce_if(task_var.present)
                else:
                    task_start = task_var.start
                    task_end = task_var.end

                task_starts.append(task_start)
                task_ends.append(task_end)

            task_starts = [self._task_vars[task].start for task in job.tasks]
            task_ends = [self._task_vars[task].end for task in job.tasks]

            model.add_min_equality(job_var.start, task_starts)
            model.add_max_equality(job_var.end, task_ends)

    def _select_one_mode(self):
        """
        Selects one mode for each task if and only if the task is present,
        and synchronizes the selected mode variable with the task variable.
        """
        model, data = self._model, self._data
        task2modes = utils.task2modes(data)

        for task in range(data.num_tasks):
            task_var = self._task_vars[task]

            # Select one mode if and only if task is present.
            presences = [self._mode_vars[m].present for m in task2modes[task]]
            model.add(sum(presences) == task_var.present)

            for mode in task2modes[task]:
                # Synchronize task var with mode var if both are present.
                mode_var = self._mode_vars[mode]
                both_present = [task_var.present, mode_var.present]

                sync_start = task_var.start == mode_var.start
                model.add(sync_start).only_enforce_if(both_present)

                sync_duration = task_var.duration == mode_var.duration
                model.add(sync_duration).only_enforce_if(both_present)

                sync_end = task_var.end == mode_var.end
                model.add(sync_end).only_enforce_if(both_present)

    def _machines_no_overlap(self):
        """
        Creates no-overlap constraints for machines.
        """
        model, data = self._model, self._data

        for idx, resource in enumerate(data.resources):
            if not isinstance(resource, Machine):
                continue

            seq_var = self._sequence_vars[idx]
            mode_vars = [var.interval for var in seq_var.mode_vars]
            model.add_no_overlap(mode_vars)

    def _renewable_capacity(self):
        """
        Creates capacity constraints for the renewable resources.
        """
        model, data = self._model, self._data
        mode_vars = self._mode_vars
        res2modes, res2demands = utils.resource2modes_demands(data)

        for idx, resource in enumerate(data.resources):
            if not isinstance(resource, Renewable):
                continue

            intervals = [mode_vars[mode].interval for mode in res2modes[idx]]
            demands = res2demands[idx]
            model.add_cumulative(intervals, demands, resource.capacity)

    def _non_renewable_capacity(self):
        """
        Creates capacity constraints for the non-renewable resources.
        """
        model, data = self._model, self._data
        mode_vars = self._mode_vars
        res2modes, res2demands = utils.resource2modes_demands(data)

        for idx, resource in enumerate(data.resources):
            if not isinstance(resource, NonRenewable):
                continue

            precenses = [mode_vars[mode].present for mode in res2modes[idx]]
            demands = res2demands[idx]
            usage = LinearExpr.weighted_sum(precenses, demands)
            model.add(usage <= resource.capacity)

    def _timing_constraints(self):
        """
        Creates constraints based on the timing relationship between tasks.
        """
        model, data = self._model, self._data

        for idx1, idx2, delay in data.constraints.start_before_start:
            task_var1 = self._task_vars[idx1]
            task_var2 = self._task_vars[idx2]
            both_present = [task_var1.present, task_var2.present]
            expr = task_var1.start + delay <= task_var2.start
            model.add(expr).only_enforce_if(both_present)

        for idx1, idx2, delay in data.constraints.start_before_end:
            task_var1 = self._task_vars[idx1]
            task_var2 = self._task_vars[idx2]
            both_present = [task_var1.present, task_var2.present]
            expr = task_var1.start + delay <= task_var2.end
            model.add(expr).only_enforce_if(both_present)

        for idx1, idx2, delay in data.constraints.end_before_start:
            task_var1 = self._task_vars[idx1]
            task_var2 = self._task_vars[idx2]
            both_present = [task_var1.present, task_var2.present]
            expr = task_var1.end + delay <= task_var2.start
            model.add(expr).only_enforce_if(both_present)

        for idx1, idx2, delay in data.constraints.end_before_end:
            task_var1 = self._task_vars[idx1]
            task_var2 = self._task_vars[idx2]
            both_present = [task_var1.present, task_var2.present]
            expr = task_var1.end + delay <= task_var2.end
            model.add(expr).only_enforce_if(both_present)

    def _identical_and_different_resource_constraints(self):
        """
        Creates constraints for identical and different resources constraints.
        """
        model, data = self._model, self._data

        for idx1, idx2 in data.constraints.identical_resources:
            for mode1, modes2 in utils.identical_modes(data, idx1, idx2):
                expr1 = self._mode_vars[mode1].present
                expr2 = sum(self._mode_vars[mode2].present for mode2 in modes2)
                model.add(expr1 <= expr2)

        for idx1, idx2 in data.constraints.different_resources:
            for mode1, modes2 in utils.different_modes(data, idx1, idx2):
                expr1 = self._mode_vars[mode1].present
                expr2 = sum(self._mode_vars[mode2].present for mode2 in modes2)
                model.add(expr1 <= expr2)

    def _activate_setup_times(self):
        """
        Activates the sequence variables for resources that have setup times.
        The ``_circuit_constraints`` function will in turn add constraints to
        the CP-SAT model to enforce setup times.
        """
        model, data = self._model, self._data
        setup_times = utils.setup_times_matrix(data)

        for idx, resource in enumerate(data.resources):
            if not isinstance(resource, Machine):
                continue

            if setup_times is not None and np.any(setup_times[idx]):
                self._sequence_vars[idx].activate(model)

    def _consecutive_constraints(self):
        """
        Creates the consecutive constraints.
        """
        model, data = self._model, self._data

        for idx1, idx2 in data.constraints.consecutive:
            intersecting = utils.intersecting_modes(data, idx1, idx2)
            for mode1, mode2, resources in intersecting:
                for resource in resources:
                    if not isinstance(data.resources[resource], Machine):
                        continue

                    seq_var = self._sequence_vars[resource]
                    seq_var.activate(model)
                    var1 = self._mode_vars[mode1]
                    var2 = self._mode_vars[mode2]

                    idx1 = seq_var.mode_vars.index(var1)
                    idx2 = seq_var.mode_vars.index(var2)
                    arc = seq_var.arcs[idx1, idx2]
                    both_present = [var1.present, var2.present]

                    model.add(arc == 1).only_enforce_if(both_present)
    
    def _flow_constraints(self):
        """
        Adds flow conservation constraints for each job based on the flow variables
        assigned to each 'add_end_before_start' precedence constraint.
        
        Assumes:
        - data.flows is a dictionary mapping task indices to one of:
            'source', 'sink', or 'intermediate'.
        - data.constraints.end_before_start is a list of precedence constraints
            where each constraint has attributes `task1` and `task2` (source and target tasks).
        """
        model, data = self._model, self._data

        if not data.flows.items():
            return

        # For each job, build the incoming and outgoing arc lists per task.
        for job in data.jobs:
            tasks_in_job = set(job.tasks)  # set for fast lookup

            # Initialize dictionaries for arcs entering and leaving each task.
            inflow = {t: set() for t in tasks_in_job}
            outflow = {t: set() for t in tasks_in_job}

            # Loop over all 'end_before_start' constraints.
            # The order of these constraints corresponds to the order of flow variables.
            for k, constraint in enumerate(data.constraints.end_before_start):
                # Consider only arcs connecting tasks in the current job.
                if constraint.task1 in tasks_in_job and constraint.task2 in tasks_in_job:
                    outflow[constraint.task1].add(self._flow_vars[k])
                    inflow[constraint.task2].add(self._flow_vars[k])

            # For each task in the job, add a flow conservation constraint.
            for t in tasks_in_job:
                flow_in = sum(inflow[t])
                flow_out = sum(outflow[t])
                # Get the assigned role (if any) from the flow data.
                role = data.flows.get(t, None)
                if role == "source":
                    model.add(flow_out == 1)
                elif role == "sink":
                    model.add(flow_in == 1)
                elif role == "intermediate":
                    model.add(flow_in == self._task_vars[t].present)
                    model.add(flow_out == self._task_vars[t].present)

    def _circuit_constraints(self):
        """
        Creates the circuit constraints for each machine, if activated by
        sequencing constraints (consecutive and setup times).
        """
        model, data = self._model, self._data
        setup_times = utils.setup_times_matrix(data)

        for idx, resource in enumerate(data.resources):
            if not isinstance(resource, Machine):
                continue

            seq_var = self._sequence_vars[idx]
            if not seq_var.is_active:
                # No sequencing constraints active. Skip the creation of
                # expensive circuit constraints.
                continue

            mode_vars = seq_var.mode_vars
            arcs = seq_var.arcs

            graph = [(u, v, var) for (u, v), var in arcs.items()]
            model.add_circuit(graph)

            for idx1, var1 in enumerate(mode_vars):
                # If the (dummy) self arc is selected, then the var must not
                # be present.
                model.add(arcs[idx1, idx1] <= ~var1.present)
                model.add(arcs[seq_var.DUMMY, seq_var.DUMMY] <= ~var1.present)

            for idx1, var1 in enumerate(mode_vars):
                for idx2, var2 in enumerate(mode_vars):
                    if idx1 == idx2:
                        continue

                    # If the arc is selected, then both tasks must be present.
                    model.add(arcs[idx1, idx2] <= var1.present)
                    model.add(arcs[idx1, idx2] <= var2.present)

                    setup = (
                        setup_times[idx, var1.task_idx, var2.task_idx]
                        if setup_times is not None
                        else 0
                    )
                    expr = var1.end + setup <= var2.start
                    model.add(expr).only_enforce_if(arcs[idx1, idx2])

    def add_constraints(self):
        """
        Adds all the constraints to the CP model.
        """
        self._job_spans_tasks()
        self._select_one_mode()
        self._machines_no_overlap()
        self._renewable_capacity()
        self._non_renewable_capacity()
        self._timing_constraints()
        self._identical_and_different_resource_constraints()
        self._activate_setup_times()
        self._consecutive_constraints()
        self._flow_constraints()

        # From here onwards we know which sequence constraints are active.
        self._circuit_constraints()
