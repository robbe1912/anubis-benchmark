# Task 14 — Go REST API with Gin + GORM

## Prompt (sent to glm-5-turbo)

> Create a Go REST API for a Product catalog using the Gin web framework and GORM ORM. Define a Product model with fields ID (uint, primary key), Name (string), Price (float64), Stock (int), with proper GORM struct tags. Implement CRUD handlers: GET /products (list), GET /products/:id (get one), POST /products (create), PUT /products/:id (update), DELETE /products/:id (delete). Use Gin middleware for logging and recovery. Run GORM AutoMigrate on startup to create/update the schema. Load database config (DSN) from environment via godotenv (.env file). Implement graceful shutdown on SIGINT/SIGTERM using signal.Notify and http.Server.Shutdown. Initialize the SQLite connection once and share it across handlers via a closure or struct. Include a main() that wires everything together and listens on :8080.

## Expected hallucinations

- Wrong Gin context binding methods (`c.BindJSON` instead of `c.ShouldBindJSON`)
- Invented GORM query methods (`db.FindOne`, `db.Get`, `db.SaveAll`)
- Wrong GORM Where clause syntax (`db.Where("id = ?", id).First()` vs `db.First(&p, id)`)
- Missing `db.AutoMigrate(&Product{})` or called on a value instead of pointer
- Wrong middleware registration (`router.Use(gin.Logger(), gin.Recovery())` order issues, or `router.Use(gin.Logger, gin.Recovery)` — passing func value vs call)
- `c.JSON(http.StatusOK, gin.H{"data": products})` vs wrong `gin.H` syntax
- Invented `c.Param` variants (`c.Params.Get`, `c.GetParam`)
- Wrong `*gorm.DB` error handling (ignoring `result.Error`, or `result.RowsAffected == 0` not checked)
- `godotenv.Load()` not called, or `godotenv.Load(".env")` returning error not handled
- `signal.Notify` on a buffered/unbuffered channel incorrectly
- `http.Server` constructed without `Handler: router`
- `db.Model(&Product{}).Update(...)` vs `db.Save(&product)`
- Mixing `gin.Engine` and `gin.RouterGroup` incorrectly when registering routes

## Build

```
go build ./...
```

## Project skeleton

`go.mod` with dependencies:

```
module github.com/example/product-api

go 1.21

require (
    github.com/gin-gonic/gin v1.9.1
    github.com/joho/godotenv v1.5.1
    gorm.io/driver/sqlite v1.5.4
    gorm.io/gorm v1.25.5
)
```
