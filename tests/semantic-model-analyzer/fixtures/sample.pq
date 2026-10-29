// Server
"sql-prod-01.internal" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]

// Sales Ledger
let
    Source = Sql.Database(#"Server", "Retail", [Query = "SELECT#(lf)#(tab)o.OrderId,#(lf)#(tab)o.OrderDate,#(lf)#(tab)o.CustomerId,#(lf)#(tab)o.StoreId,#(lf)#(tab)l.ProductId,#(lf)#(tab)l.Quantity,#(lf)#(tab)l.UnitPrice,#(lf)#(tab)l.Discount#(lf)FROM dbo.Orders AS o#(lf)INNER JOIN dbo.OrderLines AS l#(lf)#(tab)ON l.OrderId = o.OrderId#(lf)WHERE o.OrderDate >= '2023-01-01'#(lf)#(tab)AND o.Status <> 'Cancelled'"]),
    #"Changed Type" = Table.TransformColumnTypes(Source, {{"OrderDate", type date}, {"Quantity", Int64.Type}, {"UnitPrice", type number}, {"Discount", type number}}),
    #"Added Net Amount" = Table.AddColumn(#"Changed Type", "Net Amount", each [Quantity] * [UnitPrice] * (1 - [Discount]), type number),
    #"Removed Discount" = Table.RemoveColumns(#"Added Net Amount", {"Discount"})
in
    #"Removed Discount"

// Product Catalogue
let
    Source = Sql.Database(#"Server", "Retail", [Query = "SELECT ProductId, ProductName, Category, Subcategory, ListPrice#(lf)FROM dbo.Products#(lf)WHERE Discontinued = 0"]),
    #"Changed Type" = Table.TransformColumnTypes(Source, {{"ListPrice", type number}}),
    #"Renamed Columns" = Table.RenameColumns(#"Changed Type", {{"ProductName", "Product"}})
in
    #"Renamed Columns"

// Store List
let
    Source = Sql.Database(#"Server", "Retail", [Query = "SELECT StoreId, StoreName, Region, OpenedOn FROM dbo.Stores"]),
    #"Changed Type" = Table.TransformColumnTypes(Source, {{"OpenedOn", type date}})
in
    #"Changed Type"

// Entered by the commercial team each January — there is no system to read it from.
// Region Targets
let
    Source = Table.FromRows(
        {
            {"North", 2024, 4200000},
            {"South", 2024, 3850000},
            {"Midlands", 2024, 2960000},
            {"North", 2025, 4550000},
            {"South", 2025, 4100000},
            {"Midlands", 2025, 3200000}
        },
        {"Region", "Year", "Target"}
    ),
    #"Changed Type" = Table.TransformColumnTypes(Source, {{"Year", Int64.Type}, {"Target", type number}})
in
    #"Changed Type"

// Exchange Rates
let
    Source = SharePoint.Files("https://contoso.sharepoint.com/sites/Finance", [ApiVersion = 15]),
    #"Filtered Rows" = Table.SelectRows(Source, each Text.StartsWith([Name], "fx-rates")),
    #"Latest File" = Table.LastN(#"Filtered Rows", 1),
    #"Read Csv" = Csv.Document(#"Latest File"{0}[Content], [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    #"Promoted Headers" = Table.PromoteHeaders(#"Read Csv", [PromoteAllScalars = true]),
    #"Changed Type" = Table.TransformColumnTypes(#"Promoted Headers", {{"RateDate", type date}, {"Rate", type number}})
in
    #"Changed Type"

// The per-file transform the folder import below invokes for every workbook.
// Transform Budget File
let
    Source = (Workbook as binary) =>
        let
            Sheet = Excel.Workbook(Workbook, null, true),
            #"Budget Sheet" = Table.SelectRows(Sheet, each [Name] = "Budget"){0}[Data],
            #"Promoted Headers" = Table.PromoteHeaders(#"Budget Sheet", [PromoteAllScalars = true]),
            #"Changed Type" = Table.TransformColumnTypes(#"Promoted Headers", {{"Month", type date}, {"Budget", type number}})
        in
            #"Changed Type"
in
    Source

// Budget Workbooks
let
    Source = Folder.Files("\\fileserver\Finance\Budgets"),
    #"Filtered Rows" = Table.SelectRows(Source, each Text.EndsWith([Extension], ".xlsx") and not Text.StartsWith([Name], "~$")),
    #"Invoked Transform" = Table.AddColumn(#"Filtered Rows", "Budget", each #"Transform Budget File"([Content])),
    #"Removed Other Columns" = Table.SelectColumns(#"Invoked Transform", {"Name", "Budget"}),
    #"Expanded Budget" = Table.ExpandTableColumn(#"Removed Other Columns", "Budget", {"Month", "Budget"}, {"Month", "Budget Amount"})
in
    #"Expanded Budget"

// Sales Enriched
let
    Source = #"Sales Ledger",
    #"Merged Products" = Table.NestedJoin(Source, {"ProductId"}, #"Product Catalogue", {"ProductId"}, "Product Catalogue", JoinKind.LeftOuter),
    #"Expanded Product" = Table.ExpandTableColumn(#"Merged Products", "Product Catalogue", {"Product", "Category", "Subcategory"}, {"Product", "Category", "Subcategory"}),
    #"Merged Stores" = Table.NestedJoin(#"Expanded Product", {"StoreId"}, #"Store List", {"StoreId"}, "Store List", JoinKind.LeftOuter),
    #"Expanded Store" = Table.ExpandTableColumn(#"Merged Stores", "Store List", {"StoreName", "Region"}, {"Store", "Region"}),
    #"Added Rate" = Table.AddColumn(#"Expanded Store", "Rate", each List.Max(Table.SelectRows(#"Exchange Rates", (r) => r[RateDate] <= [OrderDate])[Rate]), type number),
    #"Added Amount GBP" = Table.AddColumn(#"Added Rate", "Amount GBP", each [Net Amount] * [Rate], type number),
    #"Sorted Rows" = Table.Sort(#"Expanded Store", {{"OrderDate", Order.Ascending}})
in
    #"Added Amount GBP"

// Regional Summary
let
    Source = #"Sales Enriched",
    #"Grouped Rows" = Table.Group(Source, {"Region", "Category"}, {{"Amount GBP", each List.Sum([Amount GBP]), type number}, {"Orders", each Table.RowCount(_), Int64.Type}}),
    #"Filtered Rows" = Table.SelectRows(#"Grouped Rows", each [Amount GBP] <> null and [Amount GBP] > 0),
    #"Changed Type" = Table.TransformColumnTypes(#"Filtered Rows", {{"Orders", Int64.Type}})
in
    #"Changed Type"

// Target Variance
let
    Source = #"Regional Summary",
    #"Merged Targets" = Table.NestedJoin(Source, {"Region"}, #"Region Targets", {"Region"}, "Region Targets", JoinKind.LeftOuter),
    #"Expanded Target" = Table.ExpandTableColumn(#"Merged Targets", "Region Targets", {"Target"}, {"Target"}),
    #"Added Variance" = Table.AddColumn(#"Expanded Target", "Variance", each [Amount GBP] - [Target], type number),
    #"Added Variance Pct" = Table.AddColumn(#"Added Variance", "Variance %", each if [Target] = 0 then null else [Variance] / [Target], type number)
in
    #"Added Variance Pct"

// Budget vs Actual
let
    Source = Table.Combine({
        Table.SelectColumns(Table.AddColumn(#"Regional Summary", "Measure", each "Actual", type text), {"Region", "Category", "Measure", "Amount GBP"}),
        Table.SelectColumns(Table.AddColumn(#"Budget Workbooks", "Measure", each "Budget", type text), {"Measure", "Budget Amount"})
    }),
    #"Pivoted Measure" = Table.Pivot(Source, List.Distinct(Source[Measure]), "Measure", "Amount GBP", List.Sum)
in
    #"Pivoted Measure"
