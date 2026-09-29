from collections import deque
from dataclasses import dataclass

from .schemas import CompletionRequest, DeadlockRequest


@dataclass(frozen=True)
class Transition:
    id: str
    sender: str
    receiver: str
    action: str
    message: str
    source: object
    target: object


@dataclass(frozen=True)
class Configuration:
    state_a: object
    state_b: object
    queue_a_to_b: str | None
    queue_b_to_a: str | None


@dataclass(frozen=True)
class Model:
    transitions: tuple[Transition, ...]
    outgoing: dict[str, dict[object, list[Transition]]]
    initial: Configuration
    finals: dict[str, set]


def _build_model(request: DeadlockRequest | CompletionRequest) -> Model:
    transitions = tuple(
        Transition(
            id=item.id,
            sender=item.sender,
            receiver=item.receiver,
            action=item.action,
            message=item.message,
            source=item.source,
            target=item.target,
        )
        for item in sorted(request.transitions, key=lambda transition: transition.id)
    )

    outgoing: dict[str, dict[object, list[Transition]]] = {"A": {}, "B": {}}
    for transition in transitions:
        owner = transition.sender if transition.action == "send" else transition.receiver
        outgoing[owner].setdefault(transition.source, []).append(transition)
    for owner in outgoing:
        for enabled in outgoing[owner].values():
            enabled.sort(key=lambda transition: transition.id)

    initial = Configuration(
        request.A.initial,
        request.B.initial,
        None,
        None,
    )
    finals = {"A": set(request.A.finals), "B": set(request.B.finals)}
    return Model(transitions=transitions, outgoing=outgoing, initial=initial, finals=finals)


def _is_completion(model: Model, current: Configuration) -> bool:
    return (
        current.state_a in model.finals["A"]
        and current.state_b in model.finals["B"]
        and current.queue_a_to_b is None
        and current.queue_b_to_a is None
    )


def _enabled_transitions(
    model: Model, current: Configuration
) -> list[Transition]:
    enabled = []
    for endpoint in ("A", "B"):
        state = current.state_a if endpoint == "A" else current.state_b
        for transition in model.outgoing[endpoint].get(state, ()):
            if transition.action == "send":
                own_queue = (
                    current.queue_a_to_b
                    if transition.sender == "A"
                    else current.queue_b_to_a
                )
                if own_queue is None:
                    enabled.append(transition)
            else:
                opposite_queue = (
                    current.queue_b_to_a
                    if transition.receiver == "A"
                    else current.queue_a_to_b
                )
                if opposite_queue == transition.message:
                    enabled.append(transition)

    enabled.sort(key=lambda transition: transition.id)
    return enabled


def _apply(current: Configuration, transition: Transition) -> Configuration:
    if transition.action == "send":
        if transition.sender == "A":
            return Configuration(
                transition.target,
                current.state_b,
                transition.message,
                current.queue_b_to_a,
            )
        return Configuration(
            current.state_a,
            transition.target,
            current.queue_a_to_b,
            transition.message,
        )

    if transition.receiver == "A":
        return Configuration(
            transition.target,
            current.state_b,
            current.queue_a_to_b,
            None,
        )
    return Configuration(
        current.state_a,
        transition.target,
        None,
        current.queue_b_to_a,
    )


def analyze(request: DeadlockRequest) -> dict:
    model = _build_model(request)
    initial = model.initial

    # BFS visits configurations in shortest-path order. Successors are generated
    # in transition-id order, so the first deadlock reached is the required tie-break.
    queue = deque([initial])
    parents: dict[Configuration, tuple[Configuration | None, Transition | None]] = {
        initial: (None, None)
    }
    deadlock: Configuration | None = None

    while queue:
        current = queue.popleft()

        if _is_completion(model, current):
            continue

        enabled = _enabled_transitions(model, current)

        if not enabled:
            if deadlock is None:
                deadlock = current
            continue

        for transition in enabled:
            nxt = _apply(current, transition)
            if nxt not in parents:
                parents[nxt] = (current, transition)
                queue.append(nxt)

    response = {
        "deadlock": deadlock is not None,
        "visited_states": len(parents),
        "initial": _configuration_view(initial),
        "deadlock_state": None,
        "path": None,
    }

    if deadlock is not None:
        steps = []
        cursor: Configuration | None = deadlock
        history = []
        while cursor is not None:
            parent, transition = parents[cursor]
            if transition is None:
                break
            history.append((cursor, transition))
            cursor = parent
        history.reverse()

        for index, (configuration, transition) in enumerate(history, start=1):
            steps.append(
                {
                    "index": index,
                    "transition_id": transition.id,
                    "from": transition.sender,
                    "to": transition.receiver,
                    "action": transition.action,
                    "message": transition.message,
                    **_configuration_view(configuration),
                }
            )

        response["deadlock_state"] = _configuration_view(deadlock)
        response["path"] = steps

    return response


def plan_completion(request: CompletionRequest) -> dict:
    model = _build_model(request)
    initial = model.initial

    index_by_id = {transition.id: bit for bit, transition in enumerate(model.transitions)}
    required_mask = 0
    for transition_id in request.required_transition_ids:
        required_mask |= 1 << index_by_id[transition_id]

    # Search state: configuration plus the mask of required transition ids already
    # taken (mask only tracks the ids listed in required_transition_ids).
    # Completion configurations are never expanded.
    start = (initial, 0)
    queue = deque([start])
    parents: dict[
        tuple[Configuration, int],
        tuple[tuple[Configuration, int] | None, Transition | None],
    ] = {start: (None, None)}
    goal: tuple[Configuration, int] | None = None
    if required_mask == 0 and _is_completion(model, initial):
        goal = start

    while queue:
        current, mask = queue.popleft()

        if _is_completion(model, current):
            # Completion configurations are never expanded.
            continue

        for transition in _enabled_transitions(model, current):
            nxt = _apply(current, transition)
            bit = index_by_id[transition.id]
            next_mask = mask | (1 << bit) if (required_mask >> bit) & 1 else mask
            node = (nxt, next_mask)
            if node in parents:
                continue
            parents[node] = ((current, mask), transition)
            if _is_completion(model, nxt) and next_mask == required_mask and goal is None:
                goal = node
            queue.append(node)

    response = {
        "completable": goal is not None,
        "visited_states": len(parents),
        "initial": _configuration_view(initial),
        "path": None,
    }

    if goal is not None:
        steps = []
        cursor: tuple[Configuration, int] | None = goal
        history = []
        while cursor is not None:
            parent, transition = parents[cursor]
            if transition is None:
                break
            history.append((cursor[0], transition))
            cursor = parent
        history.reverse()

        for index, (configuration, transition) in enumerate(history, start=1):
            steps.append(
                {
                    "index": index,
                    "transition_id": transition.id,
                    "from": transition.sender,
                    "to": transition.receiver,
                    "action": transition.action,
                    "message": transition.message,
                    **_configuration_view(configuration),
                }
            )

        response["path"] = steps

    return response


def _configuration_view(configuration: Configuration) -> dict:
    return {
        "state": {"A": configuration.state_a, "B": configuration.state_b},
        "queues": {
            "A_to_B": configuration.queue_a_to_b,
            "B_to_A": configuration.queue_b_to_a,
        },
    }
