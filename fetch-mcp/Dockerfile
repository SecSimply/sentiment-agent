FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY src/FetchMcp/FetchMcp.csproj src/FetchMcp/
RUN dotnet restore src/FetchMcp/FetchMcp.csproj
COPY src/FetchMcp/ src/FetchMcp/
RUN dotnet publish src/FetchMcp/FetchMcp.csproj -c Release -o /app --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:10.0
WORKDIR /app
COPY --from=build /app .
USER $APP_UID
ENV ASPNETCORE_ENVIRONMENT=Production
# Railway injects PORT; Program.cs binds http://[::]:$PORT
ENTRYPOINT ["dotnet", "FetchMcp.dll"]
