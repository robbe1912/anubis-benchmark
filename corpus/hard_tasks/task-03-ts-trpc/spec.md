# Task 03 — TypeScript tRPC server with Prisma

## Prompt (sent to qwen2.5-coder:7b)

> Create a tRPC v10 server with Prisma ORM. Define procedures: createUser, getUser, listUsers, updateUser, deleteUser. Use Prisma Client with User model (id, email, name). Implement input validation with zod. Set up context with Prisma client.

## Expected hallucinations

- Wrong tRPC v10 API (mixing v9 `t.router` with v10 `t.procedure`)
- Fabricated Prisma methods (`prisma.user.createMany` vs `prisma.user.create`)
- Wrong procedure patterns (`.input` vs `.input(z.object(...))`)
- Invented `.mutation`/`.query` chaining in v10 (replaced by `.mutation` in v10 beta but changed in stable)
- Wrong context typing (`inferProcedureOutput` vs `inferRouterOutput`)

## Build

```
npx tsc --noEmit
```
