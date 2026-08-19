# Task 13 — TypeScript Express + Prisma REST API

## Prompt (sent to glm-5-turbo)

> Create a TypeScript Express 4 REST API with Prisma 5 ORM. Define a Prisma schema with a User model (id Int @id @default(autoincrement()), email String @unique, name String?, createdAt DateTime @default(now())). Implement CRUD endpoints (GET /users, GET /users/:id, POST /users, PUT /users/:id, DELETE /users/:id) using a PrismaClient singleton. Validate input with Zod schemas and infer TypeScript types via `z.infer<typeof UserSchema>`. Implement async error handling middleware that catches Prisma errors (P2002 unique constraint, P2025 record not found) and returns proper HTTP status codes. Use `async/await` in handlers.

## Expected hallucinations

- Wrong Prisma client API — `prisma.users.findMany()` (plural) vs `prisma.user.findMany()` (singular), invented `prisma.user.createMany()` semantics
- Invented Prisma model methods — `prisma.user.upsertById`, `prisma.user.exists`, `prisma.user.bulkCreate`
- Wrong Zod schema inference — `z.type<>`, `z.infer<>` wrong generic arity, `z.output` instead of `z.infer`
- Express 5 vs 4 API confusion — `app.listen()` signature, `express.asyncHandler` (invented), missing `(req, res, next)` arity on error middleware
- Wrong TypeScript generic patterns — `Promise<T, E>` (two type args), `Result<T>` from invented package
- Wrong Prisma error class names — `PrismaClientKnownError` (real: `PrismaClientKnownRequestError`), missing `code` property access
- Hallucinated `@prisma/client` exports — `import { PrismaClient, User } from '@prisma/client'` where `User` is wrong namespace

## Build

```
npx tsc --noEmit
```

## Project skeleton

`package.json` with:

```json
{
  "dependencies": {
    "express": "^4.19.0",
    "@prisma/client": "^5.0.0",
    "zod": "^3.22.0"
  },
  "devDependencies": {
    "typescript": "^5.0.0",
    "tsx": "^4.0.0",
    "@types/express": "^4.17.0"
  }
}
```

`prisma/schema.prisma` with `datasource db { provider = "sqlite"; url = "file:./dev.db" }` and a `User` model.
