# Task 02 — Python Django REST Framework with custom permissions

## Prompt (sent to qwen2.5-coder:7b)

> Create a Django REST Framework API for a blog. Models: Post (title, content, author, created_at), Comment (post FK, author, content). Serializers with nested comments. ViewSets with custom permission: only author can edit. URL routing. Pagination.

## Expected hallucinations

- Wrong DRF serializer field types (`serializers.Char` instead of `serializers.CharField`)
- Fabricated permission classes (`IsAuthorOrReadOnly` import paths that don't exist)
- Wrong ViewSet methods (`get_query_set` vs `get_queryset`)
- Made-up pagination imports
- Invented permission `has_object_permission` signature

## Build

```
python manage.py check
```
