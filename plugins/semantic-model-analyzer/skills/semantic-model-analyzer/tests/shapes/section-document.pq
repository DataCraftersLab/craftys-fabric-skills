section Section1;

shared #"Currency Code" = "USD" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true];

shared #"Report Start" = #date(2024, 1, 1) meta [IsParameterQuery=true, Type="Date", IsParameterQueryRequired=true];

shared FnTitleCase = (raw as nullable text) as nullable text =>
    if raw = null then null
    else Text.Proper(Text.Trim(raw));

shared FnRateFor = (code as text, optional fallback as number) as number =>
    let
        rates = #table({"Code", "Rate"}, {{"USD", 1}, {"GBP", 0.79}}),
        hit = Table.SelectRows(rates, each [Code] = code)
    in
        if Table.IsEmpty(hit) then (fallback ?? 1) else hit{0}[Rate];

shared Orders = let
        Source = Csv.Document(File.Contents("C:\\data\\orders.csv"), [Delimiter=","]),
        Promoted = Table.PromoteHeaders(Source),
        Named = Table.TransformColumns(Promoted, {{"Customer", FnTitleCase}}),
        Converted = Table.AddColumn(Named, "AmountBase", each [Amount] * FnRateFor(#"Currency Code"))
    in
        Converted;
