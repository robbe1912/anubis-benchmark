# Task 04 — Go gRPC service with protobuf

## Prompt (sent to qwen2.5-coder:7b)

> Create a Go gRPC service for task management. Define proto: TaskService with CreateTask, GetTask, ListTasks, CompleteTask RPCs. Task message: id (string), title (string), completed (bool). Implement server with in-memory storage. Register service and start server on :50051.

## Expected hallucinations

- Wrong gRPC registration (`grpc.RegisterService` vs `pb.RegisterTaskServiceServer`)
- Fabricated protobuf types (`status.Errorf` with wrong codes)
- Wrong context patterns (`context.Background()` in handlers that should use the passed ctx)
- Made-up field accessor names (`Get_Title` vs `GetTitle`)
- Invented `proto.Marshal` / `proto.Unmarshal` for client
- `grpc.UnaryServerInterceptor` signature mistakes

## Build

```
go build ./...
```
