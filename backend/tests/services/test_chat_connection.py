import asyncio

from app.services.chat_connection import ChatConnection


class Socket:
    def __init__(self, fail=False):
        self.events = []
        self.fail = fail

    async def send_json(self, event):
        if self.fail:
            raise ConnectionError("disconnected")
        self.events.append(event)


def test_disconnect_keeps_execution_and_replays_only_missing_events():
    async def scenario():
        channel = ChatConnection()
        gate = asyncio.Event()
        finished = asyncio.Event()

        async def execute():
            await gate.wait()
            await channel.send_json({"event": "done", "result": "finished"})
            finished.set()

        channel.task = asyncio.create_task(execute())
        first = Socket()
        await channel.attach(first)
        await channel.send_json({"event": "progress", "step": 1})
        cursor = first.events[-1]["event_seq"]
        channel.detach(first)
        assert channel.running and not channel.stop_requested
        gate.set()
        await finished.wait()
        await channel.task
        second = Socket()
        await channel.attach(second, cursor, channel.epoch)
        assert [e["event"] for e in second.events] == ["progress_snapshot", "done", "session_sync"]
        assert second.events[-1]["running"] is False

    asyncio.run(scenario())


def test_old_connection_cannot_detach_replacement_and_failed_send_is_buffered():
    async def scenario():
        channel = ChatConnection()
        first, second = Socket(), Socket()
        await channel.attach(first)
        await channel.attach(second)
        channel.detach(first)
        await channel.send_json({"event": "progress", "step": 1})
        assert second.events[-1]["step"] == 1
        second.fail = True
        await channel.send_json({"event": "uml_review", "review_id": 42})
        third = Socket()
        await channel.attach(third, 999, "previous-server-epoch")
        assert [e["event"] for e in third.events] == ["progress_snapshot", "progress", "uml_review", "session_sync"]

    asyncio.run(scenario())


def test_fresh_page_gets_current_state_without_replaying_design_mutations():
    async def scenario():
        channel = ChatConnection()
        await channel.send_json({"event": "design_element", "data": "old mutation"})
        await channel.send_json({"event": "request_review", "review_id": 1})
        channel.resolve_review(1)
        await channel.send_json({"event": "progress", "step": 2})
        await channel.send_json({"event": "uml_review", "review_id": 2})
        socket = Socket()
        await channel.attach(socket)
        assert [e["event"] for e in socket.events] == ["progress_snapshot", "progress", "uml_review", "session_sync"]
        assert socket.events[-1]["pending_review_ids"] == [2]

    asyncio.run(scenario())


def test_progress_snapshot_restores_all_steps_with_or_without_cursor_and_resets_per_run():
    async def scenario():
        channel = ChatConnection()
        await channel.send_json({"event": "run_started", "run_id": "first"})
        for step in range(1, 30):
            await channel.send_json({"event": "progress", "step": step, "tool_calls_detail": [{"name": "read_file"}]})
        for cursor, epoch in [(0, ""), (29, channel.epoch), (30, channel.epoch)]:
            socket = Socket()
            await channel.attach(socket, cursor, epoch)
            snapshot = socket.events[0]
            assert snapshot["event"] == "progress_snapshot"
            assert snapshot["run_id"] == "first"
            assert [e["step"] for e in snapshot["steps"]] == list(range(1, 30))
        await channel.send_json({"event": "run_started", "run_id": "second"})
        await channel.send_json({"event": "progress", "step": 1})
        socket = Socket()
        await channel.attach(socket)
        assert socket.events[0]["run_id"] == "second"
        assert [e["step"] for e in socket.events[0]["steps"]] == [1]

    asyncio.run(scenario())
