from typing import Literal
from decimal import Decimal
from pydantic import BaseModel, Field, model_validator

class CommissionTier(BaseModel):
    minimum: Decimal = Field(ge=0,decimal_places=2)
    rate: Decimal = Field(ge=0,le=100,decimal_places=2)

class FinancialInput(BaseModel):
    id: str = Field(min_length=1,max_length=100)
    kind: Literal['product_cost','opening_balance','seller_contract','operating_cost','opening_stock']
    name: str = Field(min_length=1,max_length=180)
    brand: str = Field(default='',max_length=120)
    sku: str = Field(default='',max_length=100)
    effectiveDate: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    amount: Decimal | None = Field(default=None,max_digits=16,decimal_places=2)
    commissionRate: Decimal | None = Field(default=None,ge=0,le=100,decimal_places=2)
    bonusRate: Decimal | None = Field(default=None,ge=0,le=100,decimal_places=2)
    quantity: Decimal | None = Field(default=None,ge=0,decimal_places=2)
    tiers: list[CommissionTier] = Field(default_factory=list,max_length=20)
    targetPercent: Decimal | None = Field(default=None,gt=0,le=1000)
    basis: Literal['Faturado','Recebido'] = 'Faturado'
    contractReference: str = Field(default='',max_length=500)
    @model_validator(mode='after')
    def valid_input(self):
        from datetime import date
        date.fromisoformat(self.effectiveDate)
        if self.kind!='opening_balance' and self.amount is not None and self.amount<0:raise ValueError('Custo não pode ser negativo')
        if len({x.minimum for x in self.tiers})!=len(self.tiers):raise ValueError('Faixas duplicadas')
        if self.kind in ('product_cost','opening_stock') and (not self.brand or not self.sku):raise ValueError('Marca e SKU obrigatórios')
        # Contratos incompletos podem ser salvos para parametrização posterior.
        return self
