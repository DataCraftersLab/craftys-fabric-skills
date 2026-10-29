// A model of substance, built for this project.
//
// It exists because two test groups need a production-shaped model of their own.
// It deliberately carries: ODBC with multi-line SQL written
// as `#(lf)`/`#(tab)` escapes, a SQL Server options-record query, a SharePoint file source,
// an Enter Data table, three parameters, three custom functions (one with a nullable return
// ascription, one taking a table), the `??` coalesce operator, a nested `let` inside an
// `each`, a lambda that reads another query row by row, joins with expands, renames,
// retypes, a group with two aggregations, a reference chain seven layers deep, and one
// abandoned step that nothing reaches.
//
// Note every multi-word reference is written `#"Like This"`. Writing it bare is a parse
// error, and the first draft of this file did exactly that in eleven places -- which is
// worth remembering, because a fixture with parse errors in it tests nothing at all.

// Currency Code
"USD" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]

// Report Start
#date(2024, 1, 1) meta [IsParameterQuery=true, Type="Date", IsParameterQueryRequired=true]

// Row Cap
5000 meta [IsParameterQuery=true, Type="Number", IsParameterQueryRequired=false]

// FnTitleCase
(raw as nullable text) as nullable text =>
    if raw = null then null else Text.Proper(Text.Trim(raw))

// FnRateFor
(code as text, optional fallback as number) as number =>
let
    rates = #table({"Code", "Rate"}, {{"USD", 1}, {"GBP", 0.79}, {"EUR", 0.92}}),
    hit = Table.SelectRows(rates, each [Code] = code),
    rate = if Table.IsEmpty(hit) then (fallback ?? 1) else hit{0}[Rate]
in
    rate

// FnCleanTable
(source as table, optional keep as list) as table =>
let
    trimmed = Table.TransformColumns(source, {{"Customer", #"FnTitleCase"}}),
    kept = if keep = null then trimmed else Table.SelectColumns(trimmed, keep)
in
    kept

// Ledger Raw
let
    Source = Odbc.Query("dsn=Warehouse", "SELECT#(lf)#(tab)l.LedgerId,#(lf)#(tab)l.PostedOn,#(lf)#(tab)l.Customer,#(lf)#(tab)l.Amount,#(lf)#(tab)l.Currency,#(lf)#(tab)c.Region#(lf)FROM dbo.Ledger AS l#(lf)#(tab)INNER JOIN dbo.Customer AS c ON c.CustomerId = l.CustomerId#(lf)WHERE l.PostedOn >= '2024-01-01'#(lf)#(tab)AND l.Void = 0#(lf)ORDER BY l.PostedOn"),
    #"Changed Type" = Table.TransformColumnTypes(Source, {{"PostedOn", type date}, {"Amount", type number}}),
    #"Renamed Columns" = Table.RenameColumns(#"Changed Type", {{"PostedOn", "Posting Date"}}),
    #"Filtered Rows" = Table.SelectRows(#"Renamed Columns", each [Amount] <> null)
in
    #"Filtered Rows"

// Product Catalogue
let
    Source = Sql.Database("reporting-01", "Retail", [Query="SELECT ProductId, ProductName, Category, ListPrice#(lf)FROM dbo.Products#(lf)WHERE Discontinued = 0"]),
    #"Changed Type" = Table.TransformColumnTypes(Source, {{"ListPrice", type number}}),
    #"Added Custom" = Table.AddColumn(#"Changed Type", "Price Band", each if [ListPrice] > 100 then "High" else "Low", type text)
in
    #"Added Custom"

// Region Lookup
let
    Source = #table({"Region", "Region Name", "Manager"}, {
        {"EU", "Europe", "a.smith"},
        {"NA", "North America", "b.jones"},
        {"APAC", "Asia Pacific", "c.wong"}}),
    #"Changed Type" = Table.TransformColumnTypes(Source, {{"Region", type text}})
in
    #"Changed Type"

// Budget Files
let
    Source = SharePoint.Files("https://example.sharepoint.com/sites/Finance", [ApiVersion = 15]),
    #"Filtered Rows" = Table.SelectRows(Source, each Text.EndsWith([Name], ".csv")),
    #"Removed Other Columns" = Table.SelectColumns(#"Filtered Rows", {"Content", "Name"})
in
    #"Removed Other Columns"

// Budget Raw
let
    Source = #"Budget Files",
    #"Added Parsed" = Table.AddColumn(Source, "Parsed", each Csv.Document([Content], [Delimiter = ",", Encoding = 65001])),
    #"Expanded Parsed" = Table.ExpandTableColumn(#"Added Parsed", "Parsed", {"Region", "Month", "Budget"}),
    #"Removed Columns" = Table.RemoveColumns(#"Expanded Parsed", {"Content", "Parsed"}),
    #"Changed Type" = Table.TransformColumnTypes(#"Removed Columns", {{"Budget", type number}})
in
    #"Changed Type"

// Ledger Enriched
let
    Source = #"Ledger Raw",
    Cleaned = #"FnCleanTable"(Source),
    #"Added Rate" = Table.AddColumn(Cleaned, "Rate", each #"FnRateFor"([Currency], 1), type number),
    #"Added Base" = Table.AddColumn(#"Added Rate", "Amount Base", each [Amount] * [Rate], type number),
    #"Merged Region" = Table.NestedJoin(#"Added Base", {"Region"}, #"Region Lookup", {"Region"}, "rl", JoinKind.LeftOuter),
    #"Expanded Region" = Table.ExpandTableColumn(#"Merged Region", "rl", {"Region Name"}, {"Region Name"}),
    #"Abandoned Sort" = Table.Sort(#"Added Base", {{"Amount", Order.Descending}}),
    #"Filtered Window" = Table.SelectRows(#"Expanded Region", each [Posting Date] >= #"Report Start")
in
    #"Filtered Window"

// Ledger By Region
let
    Source = #"Ledger Enriched",
    #"Grouped Rows" = Table.Group(Source, {"Region Name"}, {
        {"Total Base", each List.Sum([Amount Base]), type number},
        {"Invoices", each Table.RowCount(_), Int64.Type}}),
    #"Sorted Rows" = Table.Sort(#"Grouped Rows", {{"Total Base", Order.Descending}}),
    #"Kept First" = Table.FirstN(#"Sorted Rows", #"Row Cap")
in
    #"Kept First"

// Budget Vs Actual
let
    Source = #"Ledger By Region",
    #"Merged Budget" = Table.NestedJoin(Source, {"Region Name"}, #"Budget Raw", {"Region"}, "b", JoinKind.LeftOuter),
    #"Added Budget" = Table.AddColumn(#"Merged Budget", "Budget", each List.Sum([b][Budget]), type number),
    #"Removed b" = Table.RemoveColumns(#"Added Budget", {"b"}),
    #"Added Variance" = Table.AddColumn(#"Removed b", "Variance", each [Total Base] - ([Budget] ?? 0), type number)
in
    #"Added Variance"

// Row By Row Lookup
let
    Source = #"Ledger Enriched",
    #"Added Category" = Table.AddColumn(Source, "Category", each
        let hit = Table.SelectRows(#"Product Catalogue", (p) => p[ProductName] = [Customer])
        in if Table.IsEmpty(hit) then "Unknown" else hit{0}[Category], type text),
    #"Renamed Currency" = Table.RenameColumns(#"Added Category", {{"Currency", #"Currency Code"}})
in
    #"Renamed Currency"

// Reporting Final
let
    Source = #"Budget Vs Actual",
    #"Merged Detail" = Table.NestedJoin(Source, {"Region Name"}, #"Row By Row Lookup", {"Region Name"}, "d", JoinKind.LeftOuter),
    #"Added Count" = Table.AddColumn(#"Merged Detail", "Detail Rows", each Table.RowCount([d]), Int64.Type),
    #"Removed d" = Table.RemoveColumns(#"Added Count", {"d"}),
    #"Renamed Final" = Table.RenameColumns(#"Removed d", {{"Total Base", "Actual"}}),
    #"Changed Final Type" = Table.TransformColumnTypes(#"Renamed Final", {{"Actual", type number}, {"Variance", type number}})
in
    #"Changed Final Type"
