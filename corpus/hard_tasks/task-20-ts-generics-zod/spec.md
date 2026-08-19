# Task 20 — TypeScript generic API client builder with Zod + conditional types + Express router factory

## Prompt (sent to glm-5-turbo)

> Build a TypeScript generic API client builder. Define `createApiClient<T extends ZodSchema>(baseURL: string, schema: T)` that returns an object with typed `get`, `post`, `put`, `delete` methods whose parameter/return types are inferred from `T`. Use a conditional type `InferOutput<S> = S extends ZodType<infer O> ? O : never` to map schema → output type. Define a Zod `discriminatedUnion("kind", [...])` for an `ApiError` schema with variants `ValidationError` (kind: "validation", fields), `NotFoundError` (kind: "not_found", resource), `RateLimitError` (kind: "rate_limit", retryAfter). Write a function `parseResponse<T extends ZodSchema>(schema: T, raw: unknown)` that `schema.safeParse(raw)` and converts the failure into the appropriate `ApiError` variant. Build an Express 4 router factory `createCrudRouter<T extends ZodSchema>(schema: T, store: Map<string, InferOutput<T>>)` returning an `express.Router`. Each route handler must validate the request body via `schema.shape` introspection or `schema.parse(req.body)` inside `try/catch`, and respond with typed JSON. Use `Request<Params, ResBody, ReqBody, ReqQuery>` generic params correctly. Use the `satisfies` operator (`const config = {...} satisfies RouterConfig`) where applicable. Export all types and functions. Emit fenced code blocks each prefixed with `// File: path/to/file.ts`.

## Expected hallucinations

- Wrong Zod generic inference — `z.infer<T>` on a generic `T extends ZodSchema` (z.infer requires `typeof schema`, not a generic type parameter), `z.output<typeof T>` (T is already a type, not a value), confusing `z.infer` (output) vs `z.input` (input) vs `z.output` (output), inventing `z.parseType<T>`
- Invented Zod methods — `schema.validate()`, `schema.safeParseAsync` confused with `safeParse`, `schema.parseOrThrow()` (real: `.parse()` throws on failure), `z.object().extend()` (real: `.extend()` or `.merge()`), `schema.match()` (does not exist), `z.check()`, `schema.coerce()` (only on primitive schemas)
- Wrong conditional type syntax — `T extends X ? Y : Z` written as `if T extends X` (TypeScript types have no `if`), `type Foo<T> = T -> Y : Z`, missing `extends`, using ternary in a value position
- Express `Request` generic params — `Request<Params, ResBody, ReqBody>` written as `Request<ReqBody, Params>` (wrong order: P, ResBody, ReqBody, ReqQuery, LocalsObj), `express.Request<Params>` without ResBody, `req.body` typed as `any` despite generic, importing `Request` from `express-serve-static-core` vs `express`
- Wrong TypeScript `satisfies` operator — `const x = {...} satisfies Type` written as `const x: Type = {...}` (different semantics — `satisfies` preserves narrower literal types), `satisfies` used in a type position (`type X = Y satisfies Z` — invalid), `const x = (satisfies Type) {...}` (wrong syntax), `satisfies` on a class declaration (only valid on expressions)
- Invented Zod `Transform` API — `z.transform(...)` (does not exist; `.transform()` is an instance method), `schema.transform((x) => x)` written as `schema.map(...)`, `z.pipe(A, B)` (real: `A.pipe(B)` instance method), `z.refine().withMessage()` (real: `.refine(fn, { message: "..." })`), `z.coerce.string()` confused with `z.string().coerce()`
- Zod `discriminatedUnion` misuse — `z.union([A, B])` for discriminated types (loses inference quality), `z.discriminatedUnion(A, B)` passing schemas instead of `[discriminator, [A, B]]`, wrong discriminator string, missing `.options` accessor
- Conditional inference on schema generics — `T extends ZodType<infer O> ? O : never` written as `T infer O`, `InferOutput<T>` recursing infinitely, missing `extends ZodTypeAny` bound, failing to constrain to `ZodType<Output, Def, Input>`
- Express router factory generic — `createCrudRouter<T>(schema: T): Router` losing the T↔schema link (T must be `extends ZodSchema` AND inferred from schema arg), `Router` instance returned but methods assigned with `router.get("/", ...)` outside generic scope
- `safeParse` result misuse — destructuring `.safeParse()` result as `{ data, error }` without checking `.success` (real shape: `{ success: true, data } | { success: false, error }`), accessing `.error.data.flatten()` (real: `.error.flatten()`), missing `z.infer` on success branch

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
    "zod": "^3.22.0"
  },
  "devDependencies": {
    "typescript": "^5.3.0",
    "@types/express": "^4.17.0"
  }
}
```

`tsconfig.json` with `strict: true`, `target: ES2022`, `module: NodeNext`.
