SELECT OrderId, SUM(Amount) AS Total
FROM dbo.Orders
GROUP BY OrderId
