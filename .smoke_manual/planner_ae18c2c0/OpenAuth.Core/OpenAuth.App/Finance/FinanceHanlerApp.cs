class FinanceHanlerApp : IFinanceHanlerApp
{
    public async Task Sync()
    {
        await AutoPlugin(ids);
    }

    public async Task AutoPlugin(IEnumerable<int> orderIds)
    {
        await SaveAsync(orderIds);
    }

    void Log()
    {
        Console.WriteLine(nameof(AutoPlugin));
    }
}