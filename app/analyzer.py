from collections import deque
from dataclasses import dataclass

from .schemas import DeadlockRequest


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


def analyze(request: DeadlockRequest) -> dict:
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

    outgoing = {"A": {}, "B": {}}
    for transition in transitions:
        owner = transition.sender if transition.action == "send" else transition.receiver
        outgoing[owner].setdefault(transition.source, []).append(transition)

    initial = Configuration(
        request.A.initial,
        request.B.initial,
        None,
        None,
    )
    finals = {"A": set(request.A.finals), "B": set(request.B.finals)}

    # BFS visits configurations in shortest-path order. Successors are generated
    # in transition-id order, so the first deadlock reached is the required tie-break.
    queue = deque([initial])
    parents: dict[Configuration, tuple[Configuration | None, Transition | None]] = {
        initial: (None, None)
    }
    deadlock: Configuration | None = None

    while queue:
        current = queue.popleft()

        if (
            current.state_a in finals["A"]
            and current.state_b in finals["B"]
            and current.queue_a_to_b is None
            and current.queue_b_to_a is None
        ):
            continue

        enabled = []
        for endpoint in ("A", "B"):
            state = current.state_a if endpoint == "A" else current.state_b
            for transition in outgoing[endpoint].get(state, ()):
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

        if not enabled:
            if deadlock is None:
                deadlock = current
            continue

        for transition in enabled:
            if transition.action == "send":
                if transition.sender == "A":
                    nxt = Configuration(
                        transition.target,
                        current.state_b,
                        transition.message,
                        current.queue_b_to_a,
                    )
                else:
                    nxt = Configuration(
                        current.state_a,
                        transition.target,
                        current.queue_a_to_b,
                        transition.message,
                    )
            else:
                if transition.receiver == "A":
                    nxt = Configuration(
                        transition.target,
                        current.state_b,
                        current.queue_a_to_b,
                        None,
                    )
                else:
                    nxt = Configuration(
                        current.state_a,
                        transition.target,
                        None,
                        current.queue_b_to_a,
                    )

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


def _configuration_view(configuration: Configuration) -> dict:
    return {
        "state": {"A": configuration.state_a, "B": configuration.state_b},
        "queues": {
            "A_to_B": configuration.queue_a_to_b,
            "B_to_A": configuration.queue_b_to_a,
        },
    }
