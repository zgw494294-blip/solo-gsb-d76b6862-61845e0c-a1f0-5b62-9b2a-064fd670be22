from collections import deque
from dataclasses import dataclass


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
class ProtocolModel:
    transitions: tuple[Transition, ...]
    outgoing: dict[str, dict[object, tuple[Transition, ...]]]
    initial: Configuration
    finals: dict[str, frozenset]


def build_model(request) -> ProtocolModel:
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

    initial = Configuration(
        request.A.initial,
        request.B.initial,
        None,
        None,
    )
    finals = {
        "A": frozenset(request.A.finals),
        "B": frozenset(request.B.finals),
    }

    return ProtocolModel(
        transitions=transitions,
        outgoing={
            endpoint: {source: tuple(items) for source, items in by_source.items()}
            for endpoint, by_source in outgoing.items()
        },
        initial=initial,
        finals=finals,
    )


def is_completion(configuration: Configuration, finals: dict[str, frozenset]) -> bool:
    return (
        configuration.state_a in finals["A"]
        and configuration.state_b in finals["B"]
        and configuration.queue_a_to_b is None
        and configuration.queue_b_to_a is None
    )


def enabled_transitions(
    configuration: Configuration,
    outgoing: dict[str, dict[object, tuple[Transition, ...]]],
) -> list[Transition]:
    """All transitions fireable in this configuration, sorted by transition id.

    Shares the one-slot queue semantics of the protocol: a sender may only fire
    when its own direction's slot is empty, and a receive requires the opposite
    slot's head to equal the transition's message.
    """
    enabled = []
    for endpoint in ("A", "B"):
        state = configuration.state_a if endpoint == "A" else configuration.state_b
        for transition in outgoing[endpoint].get(state, ()):
            if transition.action == "send":
                own_queue = (
                    configuration.queue_a_to_b
                    if transition.sender == "A"
                    else configuration.queue_b_to_a
                )
                if own_queue is None:
                    enabled.append(transition)
            else:
                opposite_queue = (
                    configuration.queue_b_to_a
                    if transition.receiver == "A"
                    else configuration.queue_a_to_b
                )
                if opposite_queue == transition.message:
                    enabled.append(transition)

    enabled.sort(key=lambda transition: transition.id)
    return enabled


def apply_transition(configuration: Configuration, transition: Transition) -> Configuration:
    if transition.action == "send":
        if transition.sender == "A":
            return Configuration(
                transition.target,
                configuration.state_b,
                transition.message,
                configuration.queue_b_to_a,
            )
        return Configuration(
            configuration.state_a,
            transition.target,
            configuration.queue_a_to_b,
            transition.message,
        )

    if transition.receiver == "A":
        return Configuration(
            transition.target,
            configuration.state_b,
            configuration.queue_a_to_b,
            None,
        )
    return Configuration(
        configuration.state_a,
        transition.target,
        None,
        configuration.queue_b_to_a,
    )


def analyze(request) -> dict:
    model = build_model(request)

    # BFS visits configurations in shortest-path order. Successors are generated
    # in transition-id order, so the first deadlock reached is the required tie-break.
    queue = deque([model.initial])
    parents: dict[Configuration, tuple[Configuration | None, Transition | None]] = {
        model.initial: (None, None)
    }
    deadlock: Configuration | None = None

    while queue:
        current = queue.popleft()

        if is_completion(current, model.finals):
            # Completion configurations are never expanded.
            continue

        enabled = enabled_transitions(current, model.outgoing)

        if not enabled:
            if deadlock is None:
                deadlock = current
            continue

        for transition in enabled:
            nxt = apply_transition(current, transition)
            if nxt not in parents:
                parents[nxt] = (current, transition)
                queue.append(nxt)

    response = {
        "deadlock": deadlock is not None,
        "visited_states": len(parents),
        "initial": _configuration_view(model.initial),
        "deadlock_state": None,
        "path": None,
    }

    if deadlock is not None:
        chain = _rebuild_chain(parents, deadlock, configuration_of=lambda node: node)

        steps = [
            {
                "index": index,
                "transition_id": transition.id,
                "from": transition.sender,
                "to": transition.receiver,
                "action": transition.action,
                "message": transition.message,
                **_configuration_view(configuration),
            }
            for index, (configuration, transition) in enumerate(chain, start=1)
        ]

        response["deadlock_state"] = _configuration_view(deadlock)
        response["path"] = steps

    return response


def plan_completion(request) -> dict:
    model = build_model(request)
    required = frozenset(request.required_transition_ids)

    # Search the product of configurations and the set of required transitions
    # already hit. A required transition only joins the set on its first firing;
    # other transitions leave it unchanged.
    Node = tuple[Configuration, frozenset]
    start: Node = (model.initial, frozenset())

    # BFS with successors generated in transition-id order: the first completion
    # node dequeued uses the fewest transitions, and ties resolve to the
    # lexicographically smallest transition-id sequence.
    queue = deque([start])
    parents: dict[Node, tuple[Node | None, Transition | None]] = {start: (None, None)}
    goal: Node | None = None

    while queue:
        configuration, hit = queue.popleft()

        if is_completion(configuration, model.finals):
            if goal is None and required <= hit:
                goal = (configuration, hit)
            # Completion configurations are never expanded.
            continue

        for transition in enabled_transitions(configuration, model.outgoing):
            next_configuration = apply_transition(configuration, transition)
            next_hit = hit | ({transition.id} if transition.id in required else frozenset())
            nxt: Node = (next_configuration, next_hit)
            if nxt not in parents:
                parents[nxt] = ((configuration, hit), transition)
                queue.append(nxt)

    response = {
        "completable": goal is not None,
        "visited_combinations": len(parents),
        "initial": _configuration_view(model.initial),
        "path": None,
    }

    if goal is not None:
        chain = _rebuild_chain(parents, goal, configuration_of=lambda node: node[0])
        steps = [
            {
                "index": index,
                "transition_id": transition.id,
                "from": transition.sender,
                "to": transition.receiver,
                "action": transition.action,
                "message": transition.message,
                **_configuration_view(configuration),
            }
            for index, (configuration, transition) in enumerate(chain, start=1)
        ]
        response["path"] = steps  # [] when the initial configuration already satisfies

    return response


def _rebuild_chain(parents, target, configuration_of) -> list[tuple[Configuration, Transition]]:
    """Return the (destination configuration, transition) pairs on the path to target."""
    chain: list[tuple[Configuration, Transition]] = []
    cursor = target
    while cursor is not None:
        parent, transition = parents[cursor]
        if transition is None:
            break
        chain.append((configuration_of(cursor), transition))
        cursor = parent
    chain.reverse()
    return chain


def _configuration_view(configuration: Configuration) -> dict:
    return {
        "state": {"A": configuration.state_a, "B": configuration.state_b},
        "queues": {
            "A_to_B": configuration.queue_a_to_b,
            "B_to_A": configuration.queue_b_to_a,
        },
    }
