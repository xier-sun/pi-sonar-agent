class Foo
{
    public async Task Sync()
    {
        await AutoPlugin(ids);
    }

    public async Task AutoPluginAsync(IEnumerable<int> orderIds)
    {
        await SaveAsync(orderIds);
    }
}
