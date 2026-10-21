namespace JoltIQ.OutlookCrashFixture;

internal static class HarnessContractSelfTests
{
    internal static int Run(string casesPath)
    {
        var failures = 0;
        void Test(string name, Action body)
        {
            try { body(); Console.WriteLine($"PASS {name}"); }
            catch (Exception error) { failures++; Console.Error.WriteLine($"FAIL {name}: {error.Message}"); }
        }

        Test("case catalog contains the complete acceptance matrix", () => AcceptanceContract.ValidateCatalog(casesPath));
        Test("complete synthetic evidence passes", AcceptanceContract.AssertCompleteSyntheticEvidence);
        Test("every required evidence class fails closed when missing", AcceptanceContract.AssertMissingEvidenceFails);
        Test("secret-bearing evidence is rejected", AcceptanceContract.AssertSecretsAreRejected);
        return failures == 0 ? 0 : 1;
    }
}
