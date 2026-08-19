# Task 10 — C# AutoMapper + NodaTime + CsvHelper Integration

## Prompt (sent to glm-5-turbo)

You are a senior .NET engineer. Write production-ready C# code for a data import/export service using AutoMapper, NodaTime, and CsvHelper.

Requirements:
- Console app (.NET 8) that reads a CSV of raw employee records, maps them to domain entities using AutoMapper, and exports to a summary CSV
- `EmployeeRaw` (CSV model): string Name, string Email, string HireDate (ISO 8601), string Department, string Salary
- `Employee` (domain): string Name, string Email, Instant HireDate (NodaTime), string Department, decimal Salary
- `EmployeeSummary` (export): string Name, string Email, string Department, decimal AnnualSalary, int YearsOfService
- AutoMapper Profile mapping EmployeeRaw → Employee (parse HireDate string to Instant, parse Salary string to decimal)
- AutoMapper Profile mapping Employee → EmployeeSummary (AnnualSalary = Salary * 12, YearsOfService = current years since HireDate)
- CsvHelper ClassMap for EmployeeRaw (map CSV columns: name, email, hire_date, dept, salary)
- CsvHelper ClassMap for EmployeeSummary (name, email, dept, annual_salary, years)
- Use IClock (NodaTime) injected via DI for current time calculation
- Main method: configure DI (ServiceCollection), register AutoMapper, read input.csv, map, write output.csv
- Use `// File: path` prefix for each file

## Expected hallucinations

- Wrong AutoMapper ForMember syntax (missing MapFrom, wrong lambda)
- Invented AutoMapper methods (e.g., ConvertUsing on IMappingExpression instead of MapFrom)
- NodaTime Instant.Parse confusion (wrong pattern, InUtc() misuse)
- ZonedDateTime API misuse
- CsvHelper ClassMap wrong generic syntax
- Missing PackageReference for NodaTime.Serialization or AutoMapper.Extensions
- Wrong IClock injection (SystemClock.Instance instead of injected)
- CsvHelper Map() wrong lambda parameter type
- Wrong AutoMapper Profile constructor (missing CreateMap calls)
- CsvWriter wrong flush/d dispose pattern

## Build

```bash
dotnet build
```

## Project skeleton

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="AutoMapper" Version="13.0.1" />
    <PackageReference Include="NodaTime" Version="3.2.0" />
    <PackageReference Include="CsvHelper" Version="33.0.1" />
    <PackageReference Include="Microsoft.Extensions.DependencyInjection" Version="8.0.1" />
  </ItemGroup>
</Project>
```
